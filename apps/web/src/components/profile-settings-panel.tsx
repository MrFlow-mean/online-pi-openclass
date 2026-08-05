"use client";

import clsx from "clsx";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import {
  AtSign,
  Bell,
  Building2,
  Check,
  CircleUserRound,
  Clock3,
  CreditCard,
  Eye,
  Globe2,
  KeyRound,
  LinkIcon,
  LoaderCircle,
  Mail,
  MapPin,
  MonitorSmartphone,
  Palette,
  RotateCcw,
  Send,
  ShieldCheck,
  Sparkles,
  UserRound,
} from "lucide-react";

import { useInterfaceLanguage } from "@/contexts/interface-language-context";
import { AccountSecurityActions } from "@/components/account-security-actions";
import { api, clearAuthToken, persistConnectedGuestAuthToken } from "@/lib/api";
import { userAccountLabel, userPublicEmail } from "@/lib/account";
import type { InterfaceLanguage } from "@/lib/profile-settings-state";
import {
  DEFAULT_PROFILE_SETTINGS,
  INTERFACE_LANGUAGE_OPTIONS,
  PROFILE_SETTINGS_CHANGED_EVENT,
  PROFILE_SETTINGS_STORAGE_KEY,
  normalizeProfileSettings,
  readStoredProfileSettings,
  type ProfileSettings,
} from "@/lib/profile-settings-state";
import type { AIModelCatalog, AIModelOption, CodexLoginStartResponse, CodexProviderStatus, UserView } from "@/types";

type SettingsSectionId =
  | "profile"
  | "account"
  | "appearance"
  | "notifications"
  | "billing"
  | "email"
  | "password"
  | "models"
  | "security";

type SettingsNavItem = {
  id: SettingsSectionId;
  label: string;
  icon: LucideIcon;
};

export type { ProfileSettings };

type ProfileSettingsPanelProps = {
  avatarUrl: string;
  favoriteCount: number;
  onSettingsPreviewChange?: (settings: ProfileSettings) => void;
  repositoryCount: number;
};

type SaveStatus = "idle" | "saved" | "error";
type BrowserNotificationPermission = NotificationPermission | "unsupported";

export {
  DEFAULT_PROFILE_SETTINGS,
  PROFILE_SETTINGS_CHANGED_EVENT,
  PROFILE_SETTINGS_STORAGE_KEY,
  normalizeProfileSettings,
  readStoredProfileSettings,
};

const settingsInputClass =
  "w-full rounded-md border border-stone-300 bg-white px-3 py-2 text-sm text-stone-900 shadow-sm outline-none transition placeholder:text-stone-400 focus:border-sky-500 focus:ring-2 focus:ring-sky-100";

const settingSectionClass = "border-b border-stone-200 pb-7";

const SETTINGS_PRIMARY_ICONS: Record<"profile" | "account" | "appearance" | "notifications", LucideIcon> = {
  profile: UserRound,
  account: CircleUserRound,
  appearance: Palette,
  notifications: Bell,
};

const SETTINGS_ACCOUNT_ICONS: Record<"billing" | "email" | "password" | "models" | "security", LucideIcon> = {
  billing: CreditCard,
  email: Mail,
  password: KeyRound,
  models: Sparkles,
  security: ShieldCheck,
};

function getNotificationPermission(): BrowserNotificationPermission {
  if (typeof window === "undefined" || !("Notification" in window)) {
    return "unsupported";
  }

  return Notification.permission;
}

function formatAccountDate(value: string | null | undefined, intlLocale: string, missingLabel: string) {
  if (!value) {
    return missingLabel;
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return missingLabel;
  }

  return new Intl.DateTimeFormat(intlLocale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function modelValue(model: AIModelOption) {
  return `${model.provider}:${model.model}`;
}

function modelLabel(model: AIModelOption) {
  const provider = model.provider.toUpperCase();
  return `${model.label || model.model} · ${provider}`;
}

function configuredModels(models: AIModelOption[]) {
  return models.filter((model) => model.configured);
}

export function ProfileSettingsPanel({
  avatarUrl,
  favoriteCount,
  onSettingsPreviewChange,
  repositoryCount,
}: ProfileSettingsPanelProps) {
  const router = useRouter();
  const { texts: txt, intlLocale } = useInterfaceLanguage();
  const s = txt.settings;

  const settingsPrimaryNav = useMemo<SettingsNavItem[]>(
    () =>
      txt.nav.primary.map((item) => ({
        id: item.id,
        label: item.label,
        icon: SETTINGS_PRIMARY_ICONS[item.id],
      })),
    [txt]
  );

  const settingsAccountNav = useMemo<SettingsNavItem[]>(
    () =>
      txt.nav.account.map((item) => ({
        id: item.id,
        label: item.label,
        icon: SETTINGS_ACCOUNT_ICONS[item.id],
      })),
    [txt]
  );

  const sectionTitles = useMemo(
    (): Record<SettingsSectionId, { title: string; eyebrow: string }> => ({
      profile: { title: txt.sectionTitles.profile, eyebrow: txt.sectionEyebrows.profile },
      account: { title: txt.sectionTitles.account, eyebrow: txt.sectionEyebrows.account },
      appearance: { title: txt.sectionTitles.appearance, eyebrow: txt.sectionEyebrows.appearance },
      notifications: { title: txt.sectionTitles.notifications, eyebrow: txt.sectionEyebrows.notifications },
      billing: { title: txt.sectionTitles.billing, eyebrow: txt.sectionEyebrows.billing },
      email: { title: txt.sectionTitles.email, eyebrow: txt.sectionEyebrows.email },
      password: { title: txt.sectionTitles.password, eyebrow: txt.sectionEyebrows.password },
      models: { title: txt.sectionTitles.models, eyebrow: txt.sectionEyebrows.models },
      security: { title: txt.sectionTitles.security, eyebrow: txt.sectionEyebrows.security },
    }),
    [txt]
  );

  const [activeSection, setActiveSection] = useState<SettingsSectionId>("profile");
  const [settings, setSettings] = useState<ProfileSettings>(DEFAULT_PROFILE_SETTINGS);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("idle");
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [currentUser, setCurrentUser] = useState<UserView | null>(null);
  const [isLoadingUser, setIsLoadingUser] = useState(true);
  const [userError, setUserError] = useState<string | null>(null);
  const [modelCatalog, setModelCatalog] = useState<AIModelCatalog | null>(null);
  const [isLoadingModels, setIsLoadingModels] = useState(true);
  const [modelError, setModelError] = useState<string | null>(null);
  const [codexStatus, setCodexStatus] = useState<CodexProviderStatus | null>(null);
  const [codexLogin, setCodexLogin] = useState<CodexLoginStartResponse | null>(null);
  const [codexLoginStatus, setCodexLoginStatus] = useState<string | null>(null);
  const [isCodexBusy, setIsCodexBusy] = useState(false);
  const [notificationPermission, setNotificationPermission] =
    useState<BrowserNotificationPermission>("unsupported");
  const [notificationMessage, setNotificationMessage] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      setSettings(readStoredProfileSettings());
      setNotificationPermission(getNotificationPermission());
    }, 0);

    return () => window.clearTimeout(timeoutId);
  }, []);

  useEffect(() => {
    onSettingsPreviewChange?.(settings);
  }, [onSettingsPreviewChange, settings]);

  useEffect(() => {
    let isDisposed = false;

    async function loadAccount() {
      try {
        const user = await api.getCurrentUser();
        if (isDisposed) {
          return;
        }
        setCurrentUser(user);
        setUserError(null);
      } catch (error) {
        if (!isDisposed) {
          setCurrentUser(null);
          setUserError(error instanceof Error ? error.message : s.account.fetchErrorFallback);
        }
      } finally {
        if (!isDisposed) {
          setIsLoadingUser(false);
        }
      }
    }

    async function loadModels() {
      try {
        const codex = await api.getCodexStatus();
        const catalog = await api.getAIModels();
        if (isDisposed) {
          return;
        }
        setModelCatalog(catalog);
        setCodexStatus(codex);
        setModelError(null);
      } catch (error) {
        if (!isDisposed) {
          setModelCatalog(null);
          setCodexStatus(null);
          setModelError(error instanceof Error ? error.message : s.models.fetchErrorFallback);
        }
      } finally {
        if (!isDisposed) {
          setIsLoadingModels(false);
        }
      }
    }

    void loadAccount();
    void loadModels();

    return () => {
      isDisposed = true;
    };

  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- fetch once per mount; error fallbacks reflect language when reopening Settings

  useEffect(() => {
    if (!codexLogin) {
      return;
    }
    const activeCodexLogin = codexLogin;
    let isDisposed = false;
    const intervalId = window.setInterval(async () => {
      try {
        const status = await api.getCodexLoginStatus(activeCodexLogin.login_id);
        if (isDisposed) {
          return;
        }
        if (status.status === "succeeded") {
          const [codex, catalog] = await Promise.all([api.getCodexStatus(), api.getAIModels()]);
          if (!isDisposed) {
            persistConnectedGuestAuthToken();
            setCodexLoginStatus("succeeded");
            setCodexStatus(codex);
            setModelCatalog(catalog);
            setCodexLogin(null);
          }
          return;
        }
        setCodexLoginStatus(status.status);
        if (["failed", "cancelled", "expired"].includes(status.status)) {
          setModelError(status.error || s.models.fetchErrorFallback);
          setCodexLogin(null);
        }
      } catch (error) {
        if (!isDisposed) {
          setModelError(error instanceof Error ? error.message : s.models.fetchErrorFallback);
        }
      }
    }, 2500);
    return () => {
      isDisposed = true;
      window.clearInterval(intervalId);
    };
  }, [codexLogin, s.models.fetchErrorFallback]);

  const textModels = useMemo(() => configuredModels(modelCatalog?.text ?? []), [modelCatalog]);
  const realtimeModels = useMemo(() => configuredModels(modelCatalog?.realtime ?? []), [modelCatalog]);
  const defaultTextModel = useMemo(() => modelCatalog?.text.find((model) => model.default) ?? null, [modelCatalog]);
  const defaultRealtimeModel = useMemo(
    () => modelCatalog?.realtime.find((model) => model.default) ?? null,
    [modelCatalog]
  );
  const normalizedHandle = settings.handle.trim().toLowerCase();
  const isHandleValid = /^[a-z0-9][a-z0-9-]{2,31}$/.test(normalizedHandle);
  const publicProfileUrl = `openclass.local/${normalizedHandle || DEFAULT_PROFILE_SETTINGS.handle}`;
  const filledProfileFields = [
    settings.displayName,
    settings.handle,
    settings.bio,
    settings.learningFocus,
    settings.website,
    settings.company,
    settings.location,
    ...settings.socialLinks.filter(Boolean),
  ].filter((value) => value.trim()).length;
  const profileCompleteness = Math.min(100, Math.round((filledProfileFields / 11) * 100));
  const enabledNotificationCount = [
    settings.courseActivityNotifications,
    settings.weeklyDigestNotifications,
    settings.aiResultNotifications,
    settings.resourceNotifications,
    settings.browserNotifications,
  ].filter(Boolean).length;

  function updateSetting<Key extends keyof ProfileSettings>(key: Key, value: ProfileSettings[Key]) {
    setSettings((current) => ({ ...current, [key]: value }));
    setSaveStatus("idle");
    setSaveMessage(null);
  }

  function updateSocialLink(index: number, value: string) {
    setSettings((current) => {
      const socialLinks = [...current.socialLinks];
      socialLinks[index] = value;
      return { ...current, socialLinks };
    });
    setSaveStatus("idle");
    setSaveMessage(null);
  }

  function handleSave(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();

    if (activeSection === "profile" && !isHandleValid) {
      setSaveStatus("error");
      setSaveMessage(s.handleError);
      return;
    }

    try {
      window.localStorage.setItem(PROFILE_SETTINGS_STORAGE_KEY, JSON.stringify(settings));
      window.dispatchEvent(new CustomEvent(PROFILE_SETTINGS_CHANGED_EVENT, { detail: settings }));
      setSaveStatus("saved");
      setSaveMessage(txt.save.saved);
    } catch {
      setSaveStatus("error");
      setSaveMessage(txt.save.saveFail);
    }
  }

  function handleResetAppearance() {
    setSettings((current) => ({
      ...current,
      density: DEFAULT_PROFILE_SETTINGS.density,
      highContrast: DEFAULT_PROFILE_SETTINGS.highContrast,
      largeText: DEFAULT_PROFILE_SETTINGS.largeText,
      reduceMotion: DEFAULT_PROFILE_SETTINGS.reduceMotion,
      startPage: DEFAULT_PROFILE_SETTINGS.startPage,
      theme: DEFAULT_PROFILE_SETTINGS.theme,
      visibleFocus: DEFAULT_PROFILE_SETTINGS.visibleFocus,
    }));
    setSaveStatus("idle");
    setSaveMessage(null);
  }

  async function handleRequestNotificationPermission() {
    if (typeof window === "undefined" || !("Notification" in window)) {
      setNotificationPermission("unsupported");
      setNotificationMessage(s.notifications.unsupportedBrowser);
      return;
    }

    try {
      const permission = await Notification.requestPermission();
      setNotificationPermission(permission);
      setNotificationMessage(permission === "granted" ? s.notifications.enabledOk : s.notifications.denied);
      if (permission === "granted") {
        updateSetting("browserNotifications", true);
      }
    } catch {
      setNotificationMessage(s.notifications.requestFail);
    }
  }

  async function handleSendTestNotification() {
    if (typeof window === "undefined" || !("Notification" in window)) {
      setNotificationPermission("unsupported");
      setNotificationMessage(s.notifications.unsupportedBrowser);
      return;
    }

    let permission = Notification.permission;
    if (permission === "default") {
      permission = await Notification.requestPermission();
      setNotificationPermission(permission);
    }

    if (permission !== "granted") {
      setNotificationMessage(s.notifications.needAllowFirst);
      return;
    }

    try {
      new Notification(s.notifications.testTitle, {
        body: s.notifications.testBody(settings.quietStart, settings.quietEnd),
        tag: "openclass-notification-test",
      });
      setNotificationMessage(s.notifications.testSent);
    } catch {
      setNotificationMessage(s.notifications.testSendFail);
    }
  }

  async function handleSignOut() {
    try {
      await api.logout();
    } finally {
      clearAuthToken();
      setCurrentUser(null);
      router.push("/login");
    }
  }

  async function refreshCodexModels() {
    const codex = await api.getCodexStatus(true);
    const catalog = await api.getAIModels();
    setModelCatalog(catalog);
    setCodexStatus(codex);
  }

  async function handleStartCodexLogin() {
    setIsCodexBusy(true);
    setModelError(null);
    try {
      const login = await api.startCodexDeviceLogin();
      setCodexLogin(login);
      setCodexLoginStatus("pending");
      window.open(login.verification_url, "_blank", "noopener,noreferrer");
    } catch (error) {
      setModelError(error instanceof Error ? error.message : s.models.fetchErrorFallback);
    } finally {
      setIsCodexBusy(false);
    }
  }

  async function handleCancelCodexLogin() {
    if (!codexLogin) {
      return;
    }
    setIsCodexBusy(true);
    try {
      await api.cancelCodexLogin(codexLogin.login_id);
      setCodexLogin(null);
      setCodexLoginStatus("cancelled");
    } catch (error) {
      setModelError(error instanceof Error ? error.message : s.models.fetchErrorFallback);
    } finally {
      setIsCodexBusy(false);
    }
  }

  async function handleCodexLogout() {
    setIsCodexBusy(true);
    setModelError(null);
    try {
      await api.logoutCodex();
      setCodexLogin(null);
      setCodexLoginStatus(null);
      await refreshCodexModels();
    } catch (error) {
      setModelError(error instanceof Error ? error.message : s.models.fetchErrorFallback);
    } finally {
      setIsCodexBusy(false);
    }
  }

  function renderSettingsMenuItem(item: SettingsNavItem) {
    const Icon = item.icon;
    const isActive = activeSection === item.id;

    return (
      <button
        key={item.id}
        type="button"
        onClick={() => setActiveSection(item.id)}
        className={clsx(
          "flex min-h-9 w-full items-center gap-2 rounded-md border-l-2 px-3 py-2 text-left text-sm transition",
          isActive
            ? "border-sky-500 bg-stone-100 font-semibold text-stone-950"
            : "border-transparent text-stone-700 hover:bg-stone-100 hover:text-stone-950"
        )}
      >
        <Icon className="h-4 w-4 shrink-0 text-stone-500" />
        <span className="truncate">{item.label}</span>
      </button>
    );
  }

  function renderSaveFooter(options: { disabled?: boolean; helper?: ReactNode } = {}) {
    return (
      <div className="flex flex-col gap-3 border-t border-stone-200 pt-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-h-5 text-sm">
          {saveStatus === "saved" ? (
            <span className="inline-flex items-center gap-1.5 font-medium text-emerald-700">
              <Check className="h-4 w-4" />
              {saveMessage ?? txt.save.saved}
            </span>
          ) : null}
          {saveStatus === "error" ? (
            <span className="font-medium text-red-600">{saveMessage ?? txt.save.saveFail}</span>
          ) : null}
          {saveStatus === "idle" ? options.helper : null}
        </div>
        <button
          type="submit"
          disabled={options.disabled}
          className={clsx(
            "inline-flex h-10 items-center justify-center rounded-md px-4 text-sm font-semibold text-white shadow-sm transition",
            options.disabled
              ? "cursor-not-allowed bg-stone-300"
              : "bg-emerald-600 hover:bg-emerald-700"
          )}
        >
          {txt.save.button}
        </button>
      </div>
    );
  }

  function renderSectionHeader() {
    const section = sectionTitles[activeSection];

    return (
      <div className="mb-6 border-b border-stone-200 pb-4">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-stone-400">{section.eyebrow}</p>
        <h2 className="mt-1 text-2xl font-semibold tracking-tight text-stone-950">{section.title}</h2>
      </div>
    );
  }

  function renderProfileSection() {
    const p = s.profile;
    return (
      <form className="max-w-5xl space-y-7" onSubmit={handleSave}>
        <section className={clsx(settingSectionClass, "grid gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]")}>
          <div className="space-y-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
              <Image
                src={avatarUrl}
                alt={p.avatarAlt}
                className="h-20 w-20 rounded-full border-4 border-white bg-stone-200 shadow-[0_16px_34px_rgba(15,23,42,0.08)]"
                width={80}
                height={80}
                unoptimized
              />
              <div className="min-w-0">
                <h3 className="truncate text-lg font-semibold text-stone-950">
                  {settings.displayName || DEFAULT_PROFILE_SETTINGS.displayName}
                </h3>
                <p className="mt-1 text-sm text-stone-500">@{settings.handle || DEFAULT_PROFILE_SETTINGS.handle}</p>
                <p className="mt-2 text-xs font-medium text-stone-500">
                  {p.publicLinkPrefix}
                  {publicProfileUrl}
                </p>
              </div>
            </div>

            <div>
              <div className="mb-2 flex items-center justify-between gap-3 text-sm">
                <span className="font-semibold text-stone-950">{p.completeness}</span>
                <span className="text-stone-500">{profileCompleteness}%</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-stone-200">
                <div className="h-full rounded-full bg-emerald-500" style={{ width: `${profileCompleteness}%` }} />
              </div>
            </div>

            <SegmentedSetting
              label={p.visibilityLabel}
              value={settings.profileVisibility}
              options={[
                { value: "private", label: p.visPrivate },
                { value: "workspace", label: p.visWorkspace },
                { value: "public", label: p.visPublic },
              ]}
              onChange={(value) => updateSetting("profileVisibility", value as ProfileSettings["profileVisibility"])}
            />
          </div>

          <div className="rounded-md border border-stone-200 bg-white p-4">
            <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.14em] text-stone-400">
              <Eye className="h-3.5 w-3.5" />
              {p.previewEyebrow}
            </div>
            <div className="flex items-start gap-3">
              <Image
                src={avatarUrl}
                alt=""
                className="h-12 w-12 rounded-full border border-stone-200 bg-stone-100"
                width={48}
                height={48}
                unoptimized
              />
              <div className="min-w-0">
                <p className="truncate text-base font-semibold text-stone-950">
                  {settings.displayName || DEFAULT_PROFILE_SETTINGS.displayName}
                </p>
                <p className="mt-0.5 truncate text-sm text-stone-500">@{settings.handle || DEFAULT_PROFILE_SETTINGS.handle}</p>
              </div>
            </div>
            <p className="mt-4 line-clamp-3 text-sm leading-6 text-stone-600">{settings.bio || p.bioPlaceholder}</p>
            <div className="mt-4 space-y-2 text-xs text-stone-500">
              {settings.location ? (
                <p className="flex items-center gap-2">
                  <MapPin className="h-3.5 w-3.5" />
                  {settings.location}
                </p>
              ) : null}
              {settings.company ? (
                <p className="flex items-center gap-2">
                  <Building2 className="h-3.5 w-3.5" />
                  {settings.company}
                </p>
              ) : null}
              {settings.website ? (
                <p className="flex items-center gap-2">
                  <Globe2 className="h-3.5 w-3.5" />
                  {settings.website}
                </p>
              ) : null}
              {settings.showPublicEmail && (settings.publicEmail || userPublicEmail(currentUser)) ? (
                <p className="flex items-center gap-2">
                  <AtSign className="h-3.5 w-3.5" />
                  {settings.publicEmail || userPublicEmail(currentUser)}
                </p>
              ) : null}
            </div>
            <div className="mt-4 flex flex-wrap gap-2 text-xs font-semibold text-stone-600">
              {settings.showRepositoriesOnProfile ? <span>{repositoryCount} repositories</span> : null}
              {settings.showStarsOnProfile ? <span>{favoriteCount} stars</span> : null}
            </div>
          </div>
        </section>

        <div className="grid max-w-3xl gap-5 sm:grid-cols-2">
          <label className="block">
            <span className="flex items-center justify-between gap-3 text-sm font-semibold text-stone-950">
              {p.nameLabel}
              <span className="text-xs font-medium text-stone-400">{settings.displayName.length}/40</span>
            </span>
            <input
              className={`${settingsInputClass} mt-2`}
              maxLength={40}
              value={settings.displayName}
              onChange={(event) => updateSetting("displayName", event.target.value)}
            />
          </label>

          <label className="block">
            <span className="block text-sm font-semibold text-stone-950">{p.usernameLabel}</span>
            <input
              className={clsx(`${settingsInputClass} mt-2`, !isHandleValid && "border-red-300 focus:border-red-500 focus:ring-red-100")}
              maxLength={32}
              value={settings.handle}
              onChange={(event) =>
                updateSetting(
                  "handle",
                  event.target.value
                    .toLowerCase()
                    .replace(/[^a-z0-9-]/g, "-")
                    .replace(/-+/g, "-")
                )
              }
            />
            <span className={clsx("mt-2 block text-xs leading-5", isHandleValid ? "text-stone-500" : "text-red-600")}>
              {p.usernameHint}
            </span>
          </label>
        </div>

        <label className="block max-w-3xl">
          <span className="block text-sm font-semibold text-stone-950">{p.publicEmailLabel}</span>
          <select
            className={`${settingsInputClass} mt-2 max-w-xl`}
            value={settings.publicEmail}
            onChange={(event) => updateSetting("publicEmail", event.target.value)}
          >
            <option value="">{p.publicEmailHidden}</option>
            {userPublicEmail(currentUser) ? <option value={userPublicEmail(currentUser)}>{userPublicEmail(currentUser)}</option> : null}
          </select>
        </label>

        <label className="block max-w-3xl">
          <span className="flex items-center justify-between gap-3 text-sm font-semibold text-stone-950">
            {p.bioLabel}
            <span className="text-xs font-medium text-stone-400">{settings.bio.length}/160</span>
          </span>
          <textarea
            className={`${settingsInputClass} mt-2 min-h-28 resize-y leading-6`}
            maxLength={160}
            value={settings.bio}
            onChange={(event) => updateSetting("bio", event.target.value)}
            placeholder={p.bioInputPlaceholder}
          />
        </label>

        <label className="block max-w-3xl">
          <span className="block text-sm font-semibold text-stone-950">{p.focusLabel}</span>
          <input
            className={`${settingsInputClass} mt-2`}
            value={settings.learningFocus}
            onChange={(event) => updateSetting("learningFocus", event.target.value)}
          />
        </label>

        <div className="grid max-w-3xl gap-5 sm:grid-cols-2">
          <label className="block">
            <span className="block text-sm font-semibold text-stone-950">{p.urlLabel}</span>
            <input
              className={`${settingsInputClass} mt-2`}
              value={settings.website}
              onChange={(event) => updateSetting("website", event.target.value)}
              placeholder="https://openclass.local/profile"
            />
          </label>

          <label className="block">
            <span className="block text-sm font-semibold text-stone-950">{p.locationLabel}</span>
            <input
              className={`${settingsInputClass} mt-2`}
              value={settings.location}
              onChange={(event) => updateSetting("location", event.target.value)}
              placeholder={p.locationPlaceholder}
            />
          </label>
        </div>

        <label className="block max-w-3xl">
          <span className="block text-sm font-semibold text-stone-950">{p.companyLabel}</span>
          <input
            className={`${settingsInputClass} mt-2 max-w-xl`}
            value={settings.company}
            onChange={(event) => updateSetting("company", event.target.value)}
          />
        </label>

        <div className="max-w-3xl">
          <h3 className="text-sm font-semibold text-stone-950">{p.socialTitle}</h3>
          <div className="mt-2 space-y-2">
            {settings.socialLinks.map((link, index) => (
              <div key={index} className="flex max-w-2xl items-center gap-2">
                <LinkIcon className="h-4 w-4 shrink-0 text-stone-500" />
                <input
                  className={settingsInputClass}
                  value={link}
                  onChange={(event) => updateSocialLink(index, event.target.value)}
                  placeholder={p.socialPlaceholder(index + 1)}
                />
              </div>
            ))}
          </div>
        </div>

        <section className="max-w-3xl space-y-5 border-y border-stone-200 py-5">
          <ToggleSetting
            title={p.toggleEmail}
            description={p.toggleEmailDesc}
            enabled={settings.showPublicEmail}
            onChange={(value) => updateSetting("showPublicEmail", value)}
          />
          <ToggleSetting
            title={p.toggleSocial}
            description={p.toggleSocialDesc}
            enabled={settings.showSocialLinks}
            onChange={(value) => updateSetting("showSocialLinks", value)}
          />
          <ToggleSetting
            title={p.toggleRepos}
            description={p.toggleReposDesc}
            enabled={settings.showRepositoriesOnProfile}
            onChange={(value) => updateSetting("showRepositoriesOnProfile", value)}
          />
          <ToggleSetting
            title={p.toggleStars}
            description={p.toggleStarsDesc}
            enabled={settings.showStarsOnProfile}
            onChange={(value) => updateSetting("showStarsOnProfile", value)}
          />
        </section>

        {renderSaveFooter({
          disabled: !isHandleValid,
          helper: (
            <span className="text-stone-500">
              {isHandleValid ? p.saveHelperOk(publicProfileUrl) : p.saveHelperInvalid}
            </span>
          ),
        })}
      </form>
    );
  }

  function renderAccountSection() {
    const a = s.account;
    return (
      <div className="max-w-3xl space-y-7">
        <section className={settingSectionClass}>
          {isLoadingUser ? (
            <div className="inline-flex items-center gap-2 text-sm text-stone-500">
              <LoaderCircle className="h-4 w-4 animate-spin" />
              {a.loading}
            </div>
          ) : currentUser ? (
            <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
              <div className="min-w-0">
                <p className="truncate text-lg font-semibold text-stone-950">{userAccountLabel(currentUser)}</p>
                <p className="mt-1 text-sm text-stone-500">
                  {`${currentUser.role === "admin" ? a.roleAdmin : a.roleMember} · ${a.createdLabel} `}
                  {formatAccountDate(currentUser.created_at, intlLocale, a.dateMissing)}
                </p>
              </div>
              <button
                type="button"
                onClick={handleSignOut}
                className="inline-flex h-10 items-center justify-center rounded-md border border-stone-200 bg-white px-4 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
              >
                {a.signOut}
              </button>
            </div>
          ) : (
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-sm text-stone-600">{userError || a.guestMessage}</p>
              <Link
                href="/login"
                className="inline-flex h-10 items-center justify-center rounded-md bg-stone-950 px-4 text-sm font-semibold text-white"
              >
                {a.goLogin}
              </Link>
            </div>
          )}
        </section>

        <section className="grid gap-3 sm:grid-cols-3">
          <MetricTile label={a.metricRepos} value={repositoryCount} />
          <MetricTile label={a.metricStars} value={favoriteCount} />
          <MetricTile
            label={a.metricLastLogin}
            value={currentUser?.last_login_at ? formatAccountDate(currentUser.last_login_at, intlLocale, a.dateMissing) : a.dateMissing}
          />
        </section>

        <section className="space-y-3">
          <h3 className="text-sm font-semibold text-stone-950">{a.shortcutsTitle}</h3>
          <div className="flex flex-wrap gap-2">
            <Link
              href="/studio"
              className="inline-flex h-10 items-center rounded-md border border-stone-200 bg-white px-4 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
            >
              {a.openStudio}
            </Link>
            {currentUser?.role === "admin" ? (
              <Link
                href="/admin"
                className="inline-flex h-10 items-center rounded-md border border-stone-200 bg-white px-4 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
              >
                {a.openAdmin}
              </Link>
            ) : null}
          </div>
        </section>
      </div>
    );
  }

  function renderAppearanceSection() {
    return (
      <form className="max-w-5xl space-y-7" onSubmit={handleSave}>
        <section className="border-b border-stone-200 pb-7">
          <SegmentedSetting
            label={txt.appearance.interfaceLanguageLabel}
            value={settings.interfaceLanguage}
            options={INTERFACE_LANGUAGE_OPTIONS}
            onChange={(value) => {
              const nextLang = (value === "en" ? "en" : "zh-CN") as InterfaceLanguage;
              setSettings((current) => {
                const next = { ...current, interfaceLanguage: nextLang };
                try {
                  window.localStorage.setItem(PROFILE_SETTINGS_STORAGE_KEY, JSON.stringify(next));
                  window.dispatchEvent(new CustomEvent(PROFILE_SETTINGS_CHANGED_EVENT, { detail: next }));
                } catch {
                  // ignore quota / privacy errors
                }
                setSaveStatus("idle");
                setSaveMessage(null);
                return next;
              });
            }}
          />
        </section>

        <section className={clsx(settingSectionClass, "grid gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]")}>
          <div>
            <h3 className="text-sm font-semibold text-stone-950">{txt.appearance.theme}</h3>
            <div className="mt-3 grid gap-3 sm:grid-cols-3">
              <ThemeOption
                active={settings.theme === "system"}
                label={txt.appearance.themeSystem}
                swatches={["#f8fafc", "#0f172a", "#0ea5e9"]}
                onClick={() => updateSetting("theme", "system")}
              />
              <ThemeOption
                active={settings.theme === "light"}
                label={txt.appearance.themeLight}
                swatches={["#ffffff", "#e7eef8", "#2563eb"]}
                onClick={() => updateSetting("theme", "light")}
              />
              <ThemeOption
                active={settings.theme === "warm"}
                label={txt.appearance.themeWarm}
                swatches={["#fff7ed", "#f5d0a5", "#059669"]}
                onClick={() => updateSetting("theme", "warm")}
              />
            </div>
          </div>

          <div
            className={clsx(
              "rounded-md border p-4",
              settings.theme === "light" && "border-slate-200 bg-slate-50",
              settings.theme !== "light" && "border-stone-200 bg-white",
              settings.highContrast && "border-stone-950"
            )}
          >
            <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.14em] text-stone-400">
              <MonitorSmartphone className="h-3.5 w-3.5" />
              {txt.appearance.livePreview}
            </div>
            <div className={clsx("rounded-md border bg-white p-3", settings.density === "compact" ? "space-y-2" : "space-y-3")}>
              <p className={clsx("font-semibold text-stone-950", settings.largeText ? "text-base" : "text-sm")}>
                {settings.displayName || DEFAULT_PROFILE_SETTINGS.displayName}
              </p>
              <p className={clsx("leading-6 text-stone-600", settings.largeText ? "text-sm" : "text-xs")}>
                {settings.bio || DEFAULT_PROFILE_SETTINGS.bio}
              </p>
              <div className="flex flex-wrap gap-1.5">
                <span className="rounded-full bg-sky-50 px-2 py-1 text-[11px] font-semibold text-sky-700">{txt.appearance.previewTagAi}</span>
                <span className="rounded-full bg-emerald-50 px-2 py-1 text-[11px] font-semibold text-emerald-700">
                  {txt.appearance.previewTagLesson}
                </span>
              </div>
            </div>
          </div>
        </section>

        <section className="grid gap-6 border-b border-stone-200 pb-7 lg:grid-cols-2">
          <SegmentedSetting
            label={txt.appearance.density}
            value={settings.density}
            options={[
              { value: "comfortable", label: txt.appearance.densityComfortable },
              { value: "compact", label: txt.appearance.densityCompact },
            ]}
            onChange={(value) => updateSetting("density", value as ProfileSettings["density"])}
          />

          <SegmentedSetting
            label={txt.appearance.startPage}
            value={settings.startPage}
            options={[
              { value: "home", label: txt.appearance.startHome },
              { value: "studio", label: txt.appearance.startStudio },
              { value: "profile", label: txt.appearance.startProfile },
            ]}
            onChange={(value) => updateSetting("startPage", value as ProfileSettings["startPage"])}
          />
        </section>

        <section className="space-y-5 border-b border-stone-200 pb-7">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h3 className="text-sm font-semibold text-stone-950">{txt.appearance.readingAssistTitle}</h3>
              <p className="mt-1 text-sm text-stone-500">{txt.appearance.readingAssistSubtitle}</p>
            </div>
            <button
              type="button"
              onClick={handleResetAppearance}
              className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-stone-200 bg-white px-3 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
            >
              <RotateCcw className="h-4 w-4" />
              {txt.appearance.resetAppearance}
            </button>
          </div>
          <ToggleSetting
            title={txt.appearance.reduceMotion}
            description={txt.appearance.reduceMotionDesc}
            enabled={settings.reduceMotion}
            onChange={(value) => updateSetting("reduceMotion", value)}
          />
          <ToggleSetting
            title={txt.appearance.highContrast}
            description={txt.appearance.highContrastDesc}
            enabled={settings.highContrast}
            onChange={(value) => updateSetting("highContrast", value)}
          />
          <ToggleSetting
            title={txt.appearance.largeText}
            description={txt.appearance.largeTextDesc}
            enabled={settings.largeText}
            onChange={(value) => updateSetting("largeText", value)}
          />
          <ToggleSetting
            title={txt.appearance.visibleFocus}
            description={txt.appearance.visibleFocusDesc}
            enabled={settings.visibleFocus}
            onChange={(value) => updateSetting("visibleFocus", value)}
          />
        </section>

        {renderSaveFooter({ helper: <span className="text-stone-500">{txt.appearance.saveHelper}</span> })}
      </form>
    );
  }

  function renderNotificationsSection() {
    const n = s.notifications;
    return (
      <form className="max-w-5xl space-y-7" onSubmit={handleSave}>
        <section className={clsx(settingSectionClass, "grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]")}>
          <div>
            <h3 className="text-sm font-semibold text-stone-950">{n.centerTitle}</h3>
            <p className="mt-1 text-sm leading-6 text-stone-500">
              {n.centerSummary(enabledNotificationCount, settings.quietStart, settings.quietEnd)}
            </p>
          </div>
          <div className="rounded-md border border-stone-200 bg-white px-4 py-3">
            <p className="text-xs font-semibold text-stone-500">{n.browserPermTitle}</p>
            <p className="mt-1 text-sm font-semibold text-stone-950">
              {notificationPermission === "unsupported"
                ? n.permUnsupported
                : notificationPermission === "granted"
                  ? n.permGranted
                  : notificationPermission === "denied"
                    ? n.permDenied
                    : n.permDefault}
            </p>
          </div>
        </section>

        <section className="grid gap-6 border-b border-stone-200 pb-7 lg:grid-cols-[minmax(0,1fr)_18rem]">
          <div className="space-y-5">
            <ToggleSetting
              title={n.desktopTitle}
              description={n.desktopDesc}
              enabled={settings.browserNotifications}
              onChange={(value) => updateSetting("browserNotifications", value)}
            />
            <SegmentedSetting
              label={n.frequencyLabel}
              value={settings.notificationFrequency}
              options={[
                { value: "instant", label: n.freqInstant },
                { value: "hourly", label: n.freqHourly },
                { value: "daily", label: n.freqDaily },
              ]}
              onChange={(value) => updateSetting("notificationFrequency", value as ProfileSettings["notificationFrequency"])}
            />
          </div>

          <div className="space-y-2">
            <button
              type="button"
              onClick={() => void handleRequestNotificationPermission()}
              className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-md border border-stone-200 bg-white px-4 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
            >
              <Bell className="h-4 w-4" />
              {n.allowBrowserBtn}
            </button>
            <button
              type="button"
              onClick={() => void handleSendTestNotification()}
              className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-md bg-stone-950 px-4 text-sm font-semibold text-white transition hover:bg-stone-800"
            >
              <Send className="h-4 w-4" />
              {n.sendTestBtn}
            </button>
            {notificationMessage ? <p className="text-sm leading-6 text-stone-500">{notificationMessage}</p> : null}
          </div>
        </section>

        <section className="grid gap-5 lg:grid-cols-2">
          <ToggleSetting
            title={n.courseActivityTitle}
            description={n.courseActivityDesc}
            enabled={settings.courseActivityNotifications}
            onChange={(value) => updateSetting("courseActivityNotifications", value)}
          />
          <ToggleSetting
            title={n.weeklyTitle}
            description={n.weeklyDesc}
            enabled={settings.weeklyDigestNotifications}
            onChange={(value) => updateSetting("weeklyDigestNotifications", value)}
          />
          <ToggleSetting
            title={n.aiTitle}
            description={n.aiDesc}
            enabled={settings.aiResultNotifications}
            onChange={(value) => updateSetting("aiResultNotifications", value)}
          />
          <ToggleSetting
            title={n.resourceTitle}
            description={n.resourceDesc}
            enabled={settings.resourceNotifications}
            onChange={(value) => updateSetting("resourceNotifications", value)}
          />
        </section>

        <section className="grid gap-4 border-y border-stone-200 py-6 sm:grid-cols-2">
          <label className="block">
            <span className="flex items-center gap-2 text-sm font-semibold text-stone-950">
              <Clock3 className="h-4 w-4 text-stone-500" />
              {n.quietStart}
            </span>
            <input
              type="time"
              className={`${settingsInputClass} mt-2`}
              value={settings.quietStart}
              onChange={(event) => updateSetting("quietStart", event.target.value)}
            />
          </label>
          <label className="block">
            <span className="flex items-center gap-2 text-sm font-semibold text-stone-950">
              <Clock3 className="h-4 w-4 text-stone-500" />
              {n.quietEnd}
            </span>
            <input
              type="time"
              className={`${settingsInputClass} mt-2`}
              value={settings.quietEnd}
              onChange={(event) => updateSetting("quietEnd", event.target.value)}
            />
          </label>
        </section>

        {renderSaveFooter({ helper: <span className="text-stone-500">{n.saveFooter}</span> })}
      </form>
    );
  }

  function renderBillingSection() {
    const b = s.billing;
    return (
      <div className="max-w-3xl space-y-7">
        <section className={settingSectionClass}>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-lg font-semibold text-stone-950">{b.productTitle}</p>
              <p className="mt-1 text-sm text-stone-500">{b.licenseSubtitle}</p>
            </div>
            <span className="inline-flex w-fit items-center rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-700">
              {b.enabledBadge}
            </span>
          </div>
        </section>

        <section className="space-y-4">
          <UsageMeter label={s.account.metricRepos} value={repositoryCount} max={50} />
          <UsageMeter label={s.account.metricStars} value={favoriteCount} max={100} />
          <UsageMeter label={b.seatLabel} value={currentUser ? 1 : 0} max={1} />
        </section>

        <section className="flex flex-wrap gap-2">
          <Link
            href="/admin"
            className="inline-flex h-10 items-center rounded-md border border-stone-200 bg-white px-4 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
          >
            {b.viewAdmin}
          </Link>
          <Link
            href="/"
            className="inline-flex h-10 items-center rounded-md border border-stone-200 bg-white px-4 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
          >
            {b.backHome}
          </Link>
        </section>
      </div>
    );
  }

  function renderEmailSection() {
    const e = s.email;
    return (
      <form className="max-w-3xl space-y-6" onSubmit={handleSave}>
        <section className={settingSectionClass}>
          <label className="block">
            <span className="block text-sm font-semibold text-stone-950">{e.primaryLabel}</span>
            <input
              className={`${settingsInputClass} mt-2 max-w-xl bg-stone-50`}
              value={userPublicEmail(currentUser) || e.unbound}
              readOnly
            />
          </label>
          {currentUser?.email_verified_at ? (
            <p className="mt-4 inline-flex h-10 items-center gap-2 rounded-md border border-emerald-200 bg-emerald-50 px-4 text-sm font-semibold text-emerald-800">
              <Check className="h-4 w-4" />
              {settings.interfaceLanguage === "zh-CN" ? "Primary email has been verified" : "Primary email verified"}
            </p>
          ) : currentUser && currentUser.role !== "guest" && currentUser.auth_identities.some((identity) => identity.provider === "email") ? (
            <Link
              href="/verify-email"
              className="mt-4 inline-flex h-10 items-center rounded-md border border-stone-200 bg-white px-4 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
            >
              {settings.interfaceLanguage === "zh-CN" ? "Verify primary email" : "Verify primary email"}
            </Link>
          ) : null}
        </section>

        <ToggleSetting
          title={e.digestTitle}
          description={e.digestDesc}
          enabled={settings.emailCourseDigest}
          onChange={(value) => updateSetting("emailCourseDigest", value)}
        />
        <ToggleSetting
          title={e.aiMailTitle}
          description={e.aiMailDesc}
          enabled={settings.emailAiSummary}
          onChange={(value) => updateSetting("emailAiSummary", value)}
        />
        <ToggleSetting
          title={e.securityTitle}
          description={e.securityDesc}
          enabled={settings.emailSecurityAlerts}
          onChange={(value) => updateSetting("emailSecurityAlerts", value)}
        />

        {renderSaveFooter()}
      </form>
    );
  }

  function renderPasswordSection() {
    if (!currentUser) {
      return null;
    }
    return <AccountSecurityActions language={settings.interfaceLanguage} user={currentUser} onSignedOut={() => { setCurrentUser(null); router.push("/login"); }} />;
  }

  function renderModelsSection() {
    const m = s.models;
    const hasRealtimeModelSettings = Boolean(defaultRealtimeModel) || realtimeModels.length > 0;
    const codexAccount = codexStatus?.account;
    const codexStateLabel = codexStatus?.configured
      ? `${m.codexSignedIn}${codexAccount?.email ? ` · ${codexAccount.email}` : ""}${
          codexAccount?.plan_type ? ` · ${codexAccount.plan_type}` : ""
        }`
      : m.codexSignedOut;
    const codexHelper = !codexStatus?.enabled
      ? m.codexDisabled
      : !codexStatus?.available
        ? m.codexUnavailable
        : codexStatus.message;
    return (
      <form className="max-w-3xl space-y-7" onSubmit={handleSave}>
        <section className={settingSectionClass}>
          {isLoadingModels ? (
            <div className="inline-flex items-center gap-2 text-sm text-stone-500">
              <LoaderCircle className="h-4 w-4 animate-spin" />
              {m.loading}
            </div>
          ) : modelError ? (
            <p className="text-sm text-red-600">{modelError}</p>
          ) : (
            <div className={clsx("grid gap-3", hasRealtimeModelSettings && "sm:grid-cols-2")}>
              <ModelSummary title={m.textDefaultTitle} model={defaultTextModel} emptyLabel={m.modelNotConfigured} />
              {hasRealtimeModelSettings ? (
                <ModelSummary title={m.realtimeDefaultTitle} model={defaultRealtimeModel} emptyLabel={m.modelNotConfigured} />
              ) : null}
            </div>
          )}
        </section>

        <section className={clsx(settingSectionClass, "space-y-4")}>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h3 className="text-sm font-semibold text-stone-950">{m.codexTitle}</h3>
              <p className={clsx("mt-1 text-sm leading-6", codexStatus?.configured ? "text-emerald-700" : "text-stone-500")}>
                {codexStateLabel}
              </p>
              {codexHelper ? <p className="mt-1 text-xs leading-5 text-stone-500">{codexHelper}</p> : null}
            </div>
            <div className="flex shrink-0 flex-wrap gap-2">
              {codexStatus?.configured ? (
                <button
                  type="button"
                  disabled={isCodexBusy}
                  onClick={handleCodexLogout}
                  className="inline-flex h-9 items-center justify-center rounded-md border border-stone-200 bg-white px-3 text-xs font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {isCodexBusy ? <LoaderCircle className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : null}
                  {m.codexLogout}
                </button>
              ) : (
                <button
                  type="button"
                  disabled={isCodexBusy || !codexStatus?.enabled || !codexStatus?.available}
                  onClick={handleStartCodexLogin}
                  className="inline-flex h-9 items-center justify-center rounded-md bg-stone-950 px-3 text-xs font-semibold text-white transition hover:bg-stone-800 disabled:cursor-not-allowed disabled:bg-stone-300"
                >
                  {isCodexBusy ? <LoaderCircle className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : null}
                  {m.codexLogin}
                </button>
              )}
            </div>
          </div>
          {codexLogin ? (
            <div className="rounded-md border border-stone-200 bg-stone-50 p-3">
              <p className="text-xs font-semibold uppercase text-stone-500">{m.codexWaiting}</p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold text-stone-950">{m.codexCodeLabel}</span>
                <span className="rounded-md border border-stone-200 bg-white px-2 py-1 font-mono text-sm font-semibold text-stone-950">
                  {codexLogin.user_code}
                </span>
                <a
                  href={codexLogin.verification_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex h-8 items-center justify-center rounded-md border border-stone-200 bg-white px-2.5 text-xs font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
                >
                  {m.codexOpen}
                </a>
                <button
                  type="button"
                  onClick={handleCancelCodexLogin}
                  className="inline-flex h-8 items-center justify-center rounded-md border border-stone-200 bg-white px-2.5 text-xs font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
                >
                  {m.codexCancel}
                </button>
              </div>
              {codexLoginStatus ? <p className="mt-2 text-xs text-stone-500">{codexLoginStatus}</p> : null}
            </div>
          ) : null}
        </section>

        <label className="block">
          <span className="block text-sm font-semibold text-stone-950">{m.textPrefLabel}</span>
          <select
            className={`${settingsInputClass} mt-2 max-w-xl`}
            value={settings.preferredTextModel}
            onChange={(event) => updateSetting("preferredTextModel", event.target.value)}
          >
            <option value="auto">{m.autoOption}</option>
            {textModels.map((model) => (
              <option key={modelValue(model)} value={modelValue(model)}>
                {modelLabel(model)}
              </option>
            ))}
          </select>
        </label>

        {hasRealtimeModelSettings ? (
          <label className="block">
            <span className="block text-sm font-semibold text-stone-950">{m.realtimePrefLabel}</span>
            <select
              className={`${settingsInputClass} mt-2 max-w-xl`}
              value={settings.preferredRealtimeModel}
              onChange={(event) => updateSetting("preferredRealtimeModel", event.target.value)}
            >
              <option value="auto">{m.autoOption}</option>
              {realtimeModels.map((model) => (
                <option key={modelValue(model)} value={modelValue(model)}>
                  {modelLabel(model)}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        <section className="space-y-2">
          {(modelCatalog?.text ?? []).slice(0, 6).map((model) => (
            <ModelRow key={modelValue(model)} model={model} />
          ))}
        </section>

        {renderSaveFooter()}
      </form>
    );
  }

  function renderSecuritySection() {
    const sec = s.security;
    return (
      <form className="max-w-3xl space-y-6" onSubmit={handleSave}>
        <ToggleSetting
          title={sec.hidePathsTitle}
          description={sec.hidePathsDesc}
          enabled={settings.hideLocalPaths}
          onChange={(value) => updateSetting("hideLocalPaths", value)}
        />
        <ToggleSetting
          title={sec.confirmLinksTitle}
          description={sec.confirmLinksDesc}
          enabled={settings.confirmExternalLinks}
          onChange={(value) => updateSetting("confirmExternalLinks", value)}
        />
        <ToggleSetting
          title={sec.clearSessionTitle}
          description={sec.clearSessionDesc}
          enabled={settings.clearSessionOnExit}
          onChange={(value) => updateSetting("clearSessionOnExit", value)}
        />
        <ToggleSetting
          title={sec.discoveryTitle}
          description={sec.discoveryDesc}
          enabled={settings.allowCourseDiscovery}
          onChange={(value) => updateSetting("allowCourseDiscovery", value)}
        />
        {renderSaveFooter()}
      </form>
    );
  }

  function renderActiveSection() {
    switch (activeSection) {
      case "profile":
        return renderProfileSection();
      case "account":
        return renderAccountSection();
      case "appearance":
        return renderAppearanceSection();
      case "notifications":
        return renderNotificationsSection();
      case "billing":
        return renderBillingSection();
      case "email":
        return renderEmailSection();
      case "password":
        return renderPasswordSection();
      case "models":
        return renderModelsSection();
      case "security":
        return renderSecuritySection();
      default:
        return renderProfileSection();
    }
  }

  return (
    <div className="mx-auto grid max-w-6xl gap-6 px-4 py-6 sm:px-6 md:grid-cols-[15rem_minmax(0,1fr)]">
      <aside className="h-fit md:sticky md:top-28">
        <nav className="space-y-5" aria-label={txt.nav.navAria}>
          <section className="space-y-1">{settingsPrimaryNav.map((item) => renderSettingsMenuItem(item))}</section>
          <section className="border-t border-stone-200 pt-4">
            <h2 className="mb-2 px-3 text-xs font-semibold text-stone-500">{txt.nav.entitlementLabel}</h2>
            <div className="space-y-1">{settingsAccountNav.map((item) => renderSettingsMenuItem(item))}</div>
          </section>
        </nav>
      </aside>

      <section className="min-w-0">
        {renderSectionHeader()}
        {renderActiveSection()}
      </section>
    </div>
  );
}

function ToggleSetting({
  description,
  enabled,
  onChange,
  title,
}: {
  description: string;
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  title: string;
}) {
  return (
    <section className="flex flex-col gap-3 border-b border-stone-200 pb-5 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <h3 className="text-sm font-semibold text-stone-950">{title}</h3>
        <p className="mt-1 text-sm leading-6 text-stone-500">{description}</p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={enabled}
        onClick={() => onChange(!enabled)}
        className={clsx(
          "relative h-6 w-11 shrink-0 rounded-full transition focus:outline-none focus:ring-2 focus:ring-sky-200",
          enabled ? "bg-emerald-600" : "bg-stone-300"
        )}
      >
        <span
          className={clsx(
            "absolute top-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition",
            enabled ? "left-5" : "left-0.5"
          )}
        />
      </button>
    </section>
  );
}

function ThemeOption({
  active,
  label,
  onClick,
  swatches,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
  swatches: string[];
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        "flex min-h-20 items-center justify-between gap-3 rounded-md border bg-white px-4 py-3 text-left transition",
        active ? "border-sky-500 ring-2 ring-sky-100" : "border-stone-200 hover:border-stone-300"
      )}
    >
      <span className="text-sm font-semibold text-stone-950">{label}</span>
      <span className="flex -space-x-1.5">
        {swatches.map((color) => (
          <span key={color} className="h-6 w-6 rounded-full border border-white shadow-sm" style={{ backgroundColor: color }} />
        ))}
      </span>
    </button>
  );
}

function SegmentedSetting({
  label,
  onChange,
  options,
  value,
}: {
  label: string;
  onChange: (value: string) => void;
  options: ReadonlyArray<{ label: string; value: string }>;
  value: string;
}) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-stone-950">{label}</h3>
      <div className="mt-3 inline-flex flex-wrap rounded-md border border-stone-200 bg-white p-1">
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => onChange(option.value)}
            className={clsx(
              "min-h-8 rounded px-3 text-sm font-semibold transition",
              value === option.value ? "bg-stone-950 text-white" : "text-stone-600 hover:bg-stone-100 hover:text-stone-950"
            )}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function MetricTile({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-md border border-stone-200 bg-white px-4 py-3">
      <p className="text-xs font-semibold text-stone-500">{label}</p>
      <p className="mt-1 break-words text-lg font-semibold text-stone-950">{value}</p>
    </div>
  );
}

function UsageMeter({ label, max, value }: { label: string; max: number; value: number }) {
  const percentage = Math.min(100, Math.round((value / max) * 100));

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-3 text-sm">
        <span className="font-semibold text-stone-950">{label}</span>
        <span className="text-stone-500">
          {value}/{max}
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-stone-200">
        <div className="h-full rounded-full bg-sky-500" style={{ width: `${percentage}%` }} />
      </div>
    </div>
  );
}

function ModelSummary({
  emptyLabel,
  model,
  title,
}: {
  emptyLabel: string;
  model: AIModelOption | null;
  title: string;
}) {
  return (
    <div className="rounded-md border border-stone-200 bg-white px-4 py-3">
      <p className="text-xs font-semibold text-stone-500">{title}</p>
      <p className="mt-1 truncate text-sm font-semibold text-stone-950">{model ? modelLabel(model) : emptyLabel}</p>
    </div>
  );
}

function ModelRow({ model }: { model: AIModelOption }) {
  const { texts: txt } = useInterfaceLanguage();
  const m = txt.settings.models;
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-stone-200 bg-white px-4 py-3">
      <div className="min-w-0">
        <p className="truncate text-sm font-semibold text-stone-950">{modelLabel(model)}</p>
        <p className="mt-1 text-xs text-stone-500">{model.capability === "realtime" ? m.capabilityRealtime : m.capabilityText}</p>
      </div>
      <span
        className={clsx(
          "shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold",
          model.configured ? "bg-emerald-50 text-emerald-700" : "bg-stone-100 text-stone-500"
        )}
      >
        {model.configured ? m.modelConfigured : m.modelNotConfigured}
      </span>
    </div>
  );
}
