"use client";

import clsx from "clsx";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowUpRight,
  AtSign,
  BookOpen,
  Bookmark,
  Building2,
  ChevronDown,
  ChevronRight,
  Download,
  FolderClosed,
  GitFork,
  GitPullRequest,
  GraduationCap,
  Globe2,
  LinkIcon,
  LoaderCircle,
  MapPin,
  MoreHorizontal,
  PencilLine,
  Search,
  Settings,
  Share2,
  Star,
} from "lucide-react";

import { AccountMenu } from "@/components/account-menu";
import { BrandMark } from "@/components/brand-mark";
import {
  ProfileCollaborationPanel,
  type CollaborationProject,
} from "@/components/profile-collaboration-panel";
import { ProjectVisibilityControl } from "@/components/project-visibility-control";
import {
  DEFAULT_PROFILE_SETTINGS,
  PROFILE_SETTINGS_CHANGED_EVENT,
  PROFILE_SETTINGS_STORAGE_KEY,
  ProfileSettingsPanel,
  readStoredProfileSettings,
  type ProfileSettings,
} from "@/components/profile-settings-panel";
import { useInterfaceLanguage } from "@/contexts/interface-language-context";
import { api } from "@/lib/api";
import { homeRelativeFormat } from "@/lib/i18n/product-ui";
import {
  DEFAULT_COLLECTED_COURSE_IDS,
  OPEN_COURSE_COLLECTION_STORAGE_KEY,
  OPEN_SOURCE_COURSES,
  courseAvatarUrl,
  courseDetailHref,
  courseFullName,
  formatCompactNumber,
} from "@/lib/open-courses";
import {
  publicProjectHref,
  updateLessonVisibility,
  updatePackageVisibility,
  type ProjectVisibility,
} from "@/lib/project-visibility";
import { downloadRidoc } from "@/lib/ridoc-file";
import type { CoursePackage, Lesson, WorkspaceState } from "@/types";

type ProfileTab = "repositories" | "collaboration" | "stars" | "settings";
type RepositoryTypeFilter = "all" | "lessons" | "packages";
type ProjectMenuState =
  | { kind: "lesson"; id: string }
  | { kind: "package"; id: string }
  | null;

type ProfileHomeProps = {
  initialProjectKey?: string;
  initialTab?: ProfileTab;
};

const PROFILE_AVATAR_URL = "https://api.dicebear.com/9.x/glass/svg?seed=kai-fang-ke-tang";

function getPackageUpdatedAt(coursePackage: CoursePackage) {
  const timestamps = coursePackage.lessons
    .map((lesson) => lesson.updated_at)
    .map((value) => new Date(value).getTime())
    .filter((value) => !Number.isNaN(value));

  if (!timestamps.length) {
    return null;
  }

  return new Date(Math.max(...timestamps));
}

function getPackageTopics(coursePackage: CoursePackage) {
  const topics = new Set<string>();
  coursePackage.lessons.forEach((lesson) => {
    lesson.tags.slice(0, 3).forEach((tag) => topics.add(tag));
  });
  if (!topics.size && coursePackage.lessons.length) {
    topics.add("course");
  }
  return Array.from(topics).slice(0, 5);
}

function getLessonTopics(lesson: Lesson) {
  return lesson.tags.length ? lesson.tags.slice(0, 5) : ["lesson"];
}

function sortLessonsByUpdatedAt(lessons: Lesson[]) {
  return [...lessons].sort((left, right) => {
    const leftTime = new Date(left.updated_at).getTime();
    const rightTime = new Date(right.updated_at).getTime();
    return rightTime - leftTime;
  });
}

function matchesQuery(query: string, ...values: Array<string | null | undefined>) {
  const normalized = query.trim().toLowerCase();
  if (!normalized) {
    return true;
  }

  return values.some((value) => value?.toLowerCase().includes(normalized));
}

function persistCollectedCourseIds(courseIds: Set<string>) {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(OPEN_COURSE_COLLECTION_STORAGE_KEY, JSON.stringify(Array.from(courseIds)));
  } catch {
    // Local storage may be unavailable in private browsing contexts.
  }
}

export function ProfileHome({ initialProjectKey, initialTab = "settings" }: ProfileHomeProps) {
  const router = useRouter();
  const { texts: txt } = useInterfaceLanguage();
  const ph = txt.profileHome;

  const relFmt = useMemo(
    () => (value: string | Date | null | undefined) => homeRelativeFormat(value, txt.homeRelative, txt.lang),
    [txt]
  );

  const loadWorkspaceErrorRef = useRef(ph.loadWorkspaceError);
  const lessonOpenErrRef = useRef(ph.lessonOpenError);

  useEffect(() => {
    loadWorkspaceErrorRef.current = ph.loadWorkspaceError;
    lessonOpenErrRef.current = ph.lessonOpenError;
  });

  const [activeTab, setActiveTab] = useState<ProfileTab>(initialTab);
  const [profileSettings, setProfileSettings] = useState<ProfileSettings>(DEFAULT_PROFILE_SETTINGS);
  const [workspaceState, setWorkspaceState] = useState<WorkspaceState | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [repositoryQuery, setRepositoryQuery] = useState("");
  const [repositoryTypeFilter, setRepositoryTypeFilter] = useState<RepositoryTypeFilter>("all");
  const [collaborationProjectKey, setCollaborationProjectKey] = useState<string | null>(initialProjectKey ?? null);
  const [expandedPackageIds, setExpandedPackageIds] = useState<Set<string>>(() => new Set());
  const [starQuery, setStarQuery] = useState("");
  const [openingLessonId, setOpeningLessonId] = useState<string | null>(null);
  const [projectMenu, setProjectMenu] = useState<ProjectMenuState>(null);
  const [moveMenuLessonId, setMoveMenuLessonId] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [collectedCourseIds, setCollectedCourseIds] = useState<Set<string>>(
    () => new Set(DEFAULT_COLLECTED_COURSE_IDS)
  );

  useEffect(() => {
    let isDisposed = false;

    async function loadWorkspace() {
      try {
        const payload = await api.getWorkspace();
        if (isDisposed) {
          return;
        }
        setWorkspaceState(payload);
        setError(null);
      } catch (loadError) {
        if (!isDisposed) {
          setError(loadError instanceof Error ? loadError.message : loadWorkspaceErrorRef.current);
        }
      } finally {
        if (!isDisposed) {
          setIsLoading(false);
        }
      }
    }

    void loadWorkspace();

    return () => {
      isDisposed = true;
    };
  }, []);

  useEffect(() => {
    function closeProjectMenu(event: MouseEvent) {
      if (event.target instanceof Element && event.target.closest("[data-profile-project-menu-root]")) {
        return;
      }
      setProjectMenu(null);
      setMoveMenuLessonId(null);
    }

    function closeProjectMenuWithKeyboard(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setProjectMenu(null);
        setMoveMenuLessonId(null);
      }
    }

    document.addEventListener("mousedown", closeProjectMenu);
    document.addEventListener("keydown", closeProjectMenuWithKeyboard);
    return () => {
      document.removeEventListener("mousedown", closeProjectMenu);
      document.removeEventListener("keydown", closeProjectMenuWithKeyboard);
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      try {
        const stored = window.localStorage.getItem(OPEN_COURSE_COLLECTION_STORAGE_KEY);
        if (!stored) {
          return;
        }
        const parsed = JSON.parse(stored);
        if (Array.isArray(parsed) && parsed.every((item) => typeof item === "string")) {
          setCollectedCourseIds(new Set(parsed));
        }
      } catch {
        setCollectedCourseIds(new Set(DEFAULT_COLLECTED_COURSE_IDS));
      }
    }, 0);

    return () => window.clearTimeout(timeoutId);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const syncProfileSettings = () => {
      setProfileSettings(readStoredProfileSettings());
    };
    const timeoutId = window.setTimeout(syncProfileSettings, 0);

    function handleStorage(event: StorageEvent) {
      if (event.key === PROFILE_SETTINGS_STORAGE_KEY) {
        syncProfileSettings();
      }
    }

    window.addEventListener(PROFILE_SETTINGS_CHANGED_EVENT, syncProfileSettings);
    window.addEventListener("storage", handleStorage);

    return () => {
      window.clearTimeout(timeoutId);
      window.removeEventListener(PROFILE_SETTINGS_CHANGED_EVENT, syncProfileSettings);
      window.removeEventListener("storage", handleStorage);
    };
  }, []);

  const packages = useMemo(() => workspaceState?.packages ?? [], [workspaceState]);
  const standalonePackage = packages.find((coursePackage) => coursePackage.is_standalone) ?? packages[0] ?? null;
  const coursePackageProjects = useMemo(
    () =>
      packages.filter((coursePackage) => coursePackage.id !== standalonePackage?.id).sort((left, right) => {
        const leftTime = getPackageUpdatedAt(left)?.getTime() ?? 0;
        const rightTime = getPackageUpdatedAt(right)?.getTime() ?? 0;
        return rightTime - leftTime;
      }),
    [packages, standalonePackage?.id]
  );
  const standaloneLessonProjects = useMemo(
    () => sortLessonsByUpdatedAt(standalonePackage?.lessons ?? []),
    [standalonePackage]
  );
  const favoriteProjects = useMemo(
    () => OPEN_SOURCE_COURSES.filter((course) => collectedCourseIds.has(course.id)),
    [collectedCourseIds]
  );
  const repositoryItems = useMemo(
    () =>
      [
        ...standaloneLessonProjects.map((lesson) => ({
          id: `lesson:${lesson.id}`,
          kind: "lesson" as const,
          updatedAt: lesson.updated_at,
          lesson,
        })),
        ...coursePackageProjects.map((coursePackage) => ({
          id: `package:${coursePackage.id}`,
          kind: "package" as const,
          updatedAt: getPackageUpdatedAt(coursePackage)?.toISOString() ?? "",
          coursePackage,
        })),
      ].sort((left, right) => {
        const leftTime = new Date(left.updatedAt).getTime();
        const rightTime = new Date(right.updatedAt).getTime();
        return (Number.isNaN(rightTime) ? 0 : rightTime) - (Number.isNaN(leftTime) ? 0 : leftTime);
      }),
    [coursePackageProjects, standaloneLessonProjects]
  );
  const filteredRepositoryItems = repositoryItems.filter((item) => {
    const matchesType =
      repositoryTypeFilter === "all" ||
      (repositoryTypeFilter === "lessons" && item.kind === "lesson") ||
      (repositoryTypeFilter === "packages" && item.kind === "package");

    if (!matchesType) {
      return false;
    }

    if (item.kind === "lesson") {
      return matchesQuery(
        repositoryQuery,
        item.lesson.title,
        item.lesson.summary,
        item.lesson.board_document.title,
        item.lesson.board_document.content_text,
        getLessonTopics(item.lesson).join(" "),
        standalonePackage?.title
      );
    }

    return matchesQuery(
      repositoryQuery,
      item.coursePackage.title,
      item.coursePackage.summary,
      getPackageTopics(item.coursePackage).join(" "),
      item.coursePackage.lessons.map((lesson) => lesson.title).join(" ")
    );
  });
  const repositoryTypeFilters = useMemo(
    () => [
      { id: "all" as const, label: ph.repoFilterAll, count: repositoryItems.length },
      { id: "lessons" as const, label: ph.repoFilterLessons, count: standaloneLessonProjects.length },
      { id: "packages" as const, label: ph.repoFilterPackages, count: coursePackageProjects.length },
    ],
    [ph, repositoryItems.length, standaloneLessonProjects.length, coursePackageProjects.length]
  );
  const collaborationProjects = useMemo<CollaborationProject[]>(
    () => [
      ...standaloneLessonProjects.map((lesson) => ({
        key: `lesson:${lesson.id}`,
        kind: "lesson" as const,
        id: lesson.id,
        title: lesson.title,
        visibility: lesson.visibility,
        lessonIds: [lesson.id],
        lessonCount: 1,
      })),
      ...coursePackageProjects.map((coursePackage) => ({
        key: `package:${coursePackage.id}`,
        kind: "package" as const,
        id: coursePackage.id,
        title: coursePackage.title,
        visibility: coursePackage.visibility,
        lessonIds: coursePackage.lessons.map((lesson) => lesson.id),
        lessonCount: coursePackage.lessons.length,
      })),
    ],
    [coursePackageProjects, standaloneLessonProjects]
  );
  const repositoryCount = repositoryItems.length;
  const filteredFavoriteProjects = favoriteProjects.filter((course) =>
    matchesQuery(starQuery, courseFullName(course), course.summary, course.topics.join(" "), course.language)
  );

  const profileTabs = useMemo(
    () => [
      { id: "settings" as const, label: ph.tabSettings, icon: Settings, count: null },
      { id: "repositories" as const, label: ph.tabRepositories, icon: FolderClosed, count: repositoryCount },
      { id: "collaboration" as const, label: ph.tabCollaboration, icon: GitPullRequest, count: null },
      { id: "stars" as const, label: ph.tabStars, icon: Star, count: favoriteProjects.length },
    ],
    [ph, repositoryCount, favoriteProjects.length]
  );
  const profileDisplayName = profileSettings.displayName.trim() || DEFAULT_PROFILE_SETTINGS.displayName;
  const profileHandle = profileSettings.handle.trim() || DEFAULT_PROFILE_SETTINGS.handle;
  const profileBio = profileSettings.bio.trim() || DEFAULT_PROFILE_SETTINGS.bio;
  const profileShellClassName = clsx(
    "profile-shell min-h-screen text-stone-950",
    profileSettings.theme === "light" ? "profile-shell--light" : "profile-shell--warm",
    profileSettings.density === "compact" && "profile-shell--compact",
    profileSettings.highContrast && "profile-shell--high-contrast",
    profileSettings.largeText && "profile-shell--large-text",
    profileSettings.reduceMotion && "profile-shell--reduce-motion",
    profileSettings.visibleFocus && "profile-shell--visible-focus"
  );

  function handleToggleCollectCourse(courseId: string) {
    setCollectedCourseIds((current) => {
      const next = new Set(current);
      if (next.has(courseId)) {
        next.delete(courseId);
      } else {
        next.add(courseId);
      }
      persistCollectedCourseIds(next);
      return next;
    });
  }

  async function handleOpenLesson(lessonId: string) {
    setOpeningLessonId(lessonId);

    try {
      await api.openLesson(lessonId);
      router.push("/studio");
    } catch (openError) {
      setError(openError instanceof Error ? openError.message : lessonOpenErrRef.current);
    } finally {
      setOpeningLessonId(null);
    }
  }

  function handleProfileTabChange(tab: ProfileTab) {
    setActiveTab(tab);
    if (tab !== "collaboration") {
      setCollaborationProjectKey(null);
    }
    router.replace(`/profile?tab=${encodeURIComponent(tab)}`);
  }

  function handleSelectCollaborationProject(projectKey: string | null) {
    setActiveTab("collaboration");
    setCollaborationProjectKey(projectKey);
    router.replace(
      projectKey
        ? `/profile?tab=collaboration&project=${encodeURIComponent(projectKey)}`
        : "/profile?tab=collaboration"
    );
  }

  async function handleSetLessonVisibility(lesson: Lesson, visibility: ProjectVisibility) {
    setBusyKey(`visibility:lesson:${lesson.id}`);
    try {
      const payload = await updateLessonVisibility(lesson.id, visibility);
      setWorkspaceState(payload);
      const updatedLesson = payload.packages
        .flatMap((coursePackage) => coursePackage.lessons)
        .find((item) => item.id === lesson.id);
      setError(
        visibility === "public" && updatedLesson?.visibility !== "public"
          ? updatedLesson?.publication_review.message || "资料发布审查未通过，课程保持 Private。"
          : null
      );
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "更新课程可见权限失败");
    } finally {
      setBusyKey(null);
    }
  }

  async function handleSetPackageVisibility(coursePackage: CoursePackage, visibility: ProjectVisibility) {
    setBusyKey(`visibility:package:${coursePackage.id}`);
    try {
      const payload = await updatePackageVisibility(coursePackage.id, visibility);
      setWorkspaceState(payload);
      const updatedPackage = payload.packages.find((item) => item.id === coursePackage.id);
      setError(
        visibility === "public" && updatedPackage?.visibility !== "public"
          ? updatedPackage?.publication_review.message || "资料发布审查未通过，课程包保持 Private。"
          : null
      );
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "更新课程包可见权限失败");
    } finally {
      setBusyKey(null);
    }
  }

  async function handleShareProject(
    kind: "lesson" | "package",
    project: Pick<Lesson, "id" | "title" | "visibility"> | Pick<CoursePackage, "id" | "title" | "visibility">
  ) {
    if (typeof window === "undefined" || project.visibility !== "public") {
      return;
    }

    const url = new URL(publicProjectHref(kind, project.id), window.location.origin).toString();
    const shareData = {
      title: project.title,
      text: kind === "lesson" ? `分享课程：${project.title}` : `分享课程包：${project.title}`,
      url,
    };
    try {
      if (typeof navigator.share === "function") {
        await navigator.share(shareData);
        return;
      }
      window.prompt(kind === "lesson" ? "复制公开课程链接" : "复制课程包链接", url);
    } catch (shareError) {
      if (!(shareError instanceof DOMException && shareError.name === "AbortError")) {
        setError(shareError instanceof Error ? shareError.message : "分享课程项目失败");
      }
    }
  }

  async function handleRenameLesson(lesson: Lesson) {
    const nextTitle = window.prompt("请输入新的课程名称", lesson.title);
    if (!nextTitle?.trim() || nextTitle.trim() === lesson.title) {
      return;
    }

    setBusyKey(`rename:lesson:${lesson.id}`);
    setProjectMenu(null);
    setMoveMenuLessonId(null);
    try {
      const payload = await api.renameLesson(lesson.id, nextTitle.trim());
      setWorkspaceState(payload);
      setError(null);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "重命名课程失败");
    } finally {
      setBusyKey(null);
    }
  }

  async function handleRenamePackage(coursePackage: CoursePackage) {
    const nextTitle = window.prompt("请输入新的课程包名称", coursePackage.title);
    if (!nextTitle?.trim() || nextTitle.trim() === coursePackage.title) {
      return;
    }

    setBusyKey(`rename:package:${coursePackage.id}`);
    setProjectMenu(null);
    try {
      const payload = await api.renamePackage(coursePackage.id, nextTitle.trim());
      setWorkspaceState(payload);
      setError(null);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "重命名课程包失败");
    } finally {
      setBusyKey(null);
    }
  }

  async function handleExportLessonPackage(lesson: Lesson) {
    setBusyKey(`export:lesson:${lesson.id}`);
    setProjectMenu(null);
    setMoveMenuLessonId(null);
    try {
      const blob = await api.exportRidoc(lesson.id);
      downloadRidoc(blob, lesson);
      setError(null);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "导出课程包失败");
    } finally {
      setBusyKey(null);
    }
  }

  async function handleMoveLesson(lesson: Lesson, targetPackageId: string) {
    setBusyKey(`move:lesson:${lesson.id}`);
    setProjectMenu(null);
    setMoveMenuLessonId(null);
    try {
      const payload = await api.moveLesson(lesson.id, targetPackageId);
      setWorkspaceState(payload);
      setError(null);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "移动课程失败");
    } finally {
      setBusyKey(null);
    }
  }

  function handleTogglePackageLessons(packageId: string) {
    setExpandedPackageIds((current) => {
      const next = new Set(current);
      if (next.has(packageId)) {
        next.delete(packageId);
      } else {
        next.add(packageId);
      }
      return next;
    });
  }

  return (
    <main className={profileShellClassName}>
      <header className="sticky top-0 z-30 border-b border-stone-200 bg-[#fcfbf8]/92 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <Link
            href="/"
            className="inline-flex items-center gap-2 rounded-md px-2 py-2 text-sm font-semibold text-stone-700 transition hover:bg-stone-100 hover:text-stone-950"
          >
            <ArrowLeft className="h-4 w-4" />
            <BrandMark alt="" className="h-5 w-5 rounded bg-white" size={40} />
            {ph.backToBrand}
          </Link>

          <div className="flex items-center gap-2">
            <Link
              href="/studio"
              className="inline-flex items-center gap-2 rounded-md border border-stone-200 bg-white px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
            >
              <BookOpen className="h-4 w-4" />
              {ph.workspace}
            </Link>
            <AccountMenu compact />
          </div>
        </div>

        <nav className="mx-auto flex max-w-6xl gap-1 overflow-x-auto px-4 sm:px-6" aria-label={ph.navAria}>
          {profileTabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => handleProfileTabChange(tab.id)}
                className={clsx(
                  "inline-flex min-h-11 items-center gap-2 border-b-2 px-3 text-sm font-semibold transition",
                  isActive
                    ? "border-orange-500 text-stone-950"
                    : "border-transparent text-stone-600 hover:border-stone-300 hover:text-stone-950"
                )}
              >
                <Icon className="h-4 w-4" />
                {tab.label}
                {tab.count !== null ? (
                  <span className="rounded-full bg-stone-200 px-2 py-0.5 text-[11px] font-semibold text-stone-700">
                    {tab.count}
                  </span>
                ) : null}
              </button>
            );
          })}
        </nav>
      </header>

      {activeTab === "settings" ? (
        <ProfileSettingsPanel
          avatarUrl={PROFILE_AVATAR_URL}
          favoriteCount={favoriteProjects.length}
          onSettingsPreviewChange={setProfileSettings}
          repositoryCount={repositoryCount}
        />
      ) : (
        <div className="mx-auto grid max-w-6xl gap-6 px-4 py-8 sm:px-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <aside className="h-fit">
          <div className="flex items-start gap-4 lg:block">
            <Image
              src={PROFILE_AVATAR_URL}
              alt={ph.avatarAlt}
              className="h-24 w-24 rounded-full border-4 border-white bg-stone-200 shadow-[0_16px_34px_rgba(15,23,42,0.08)] lg:h-48 lg:w-48"
              width={192}
              height={192}
              unoptimized
            />
            <div className="min-w-0 flex-1 lg:mt-4">
              <h1 className="truncate text-2xl font-semibold tracking-tight text-stone-950">{profileDisplayName}</h1>
              <p className="mt-1 text-sm text-stone-500">@{profileHandle}</p>
              <span className="mt-3 inline-flex rounded-full border border-stone-200 bg-white px-2.5 py-1 text-xs font-semibold text-stone-500">
                {profileSettings.profileVisibility === "public"
                  ? "公开"
                  : profileSettings.profileVisibility === "workspace"
                    ? "工作区可见"
                    : "仅自己可见"}
              </span>
              <button
                type="button"
                onClick={() => handleProfileTabChange("settings")}
                className="mt-4 w-full rounded-md border border-stone-200 bg-white px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
              >
                Edit profile
              </button>
              <p className="mt-4 text-sm leading-6 text-stone-600">{profileBio}</p>
              <div className="mt-4 space-y-2 text-sm text-stone-600">
                {profileSettings.location ? (
                  <p className="flex items-center gap-2">
                    <MapPin className="h-4 w-4 text-stone-400" />
                    {profileSettings.location}
                  </p>
                ) : null}
                {profileSettings.company ? (
                  <p className="flex items-center gap-2">
                    <Building2 className="h-4 w-4 text-stone-400" />
                    {profileSettings.company}
                  </p>
                ) : null}
                {profileSettings.website ? (
                  <p className="flex items-center gap-2 break-all">
                    <Globe2 className="h-4 w-4 shrink-0 text-stone-400" />
                    {profileSettings.website}
                  </p>
                ) : null}
                {profileSettings.showPublicEmail && profileSettings.publicEmail ? (
                  <p className="flex items-center gap-2 break-all">
                    <AtSign className="h-4 w-4 shrink-0 text-stone-400" />
                    {profileSettings.publicEmail}
                  </p>
                ) : null}
                {profileSettings.showSocialLinks
                  ? profileSettings.socialLinks
                      .filter(Boolean)
                      .slice(0, 4)
                      .map((link, index) => (
                        <p key={`${link}:${index}`} className="flex items-center gap-2 break-all">
                          <LinkIcon className="h-4 w-4 shrink-0 text-stone-400" />
                          {link}
                        </p>
                      ))
                  : null}
              </div>
              <div className="mt-4 flex flex-wrap gap-x-4 gap-y-2 text-sm text-stone-600">
                {profileSettings.showRepositoriesOnProfile ? <span>{repositoryCount} repositories</span> : null}
                {profileSettings.showStarsOnProfile ? <span>{favoriteProjects.length} stars</span> : null}
              </div>
            </div>
          </div>
        </aside>

        <section className="min-w-0">
          {activeTab === "repositories" ? renderRepositories() : null}
          {activeTab === "collaboration" ? (
            <ProfileCollaborationPanel
              isLoadingProjects={isLoading}
              projects={collaborationProjects}
              selectedProjectKey={collaborationProjectKey}
              onSelectProject={handleSelectCollaborationProject}
            />
          ) : null}
          {activeTab === "stars" ? renderStars() : null}
        </section>
      </div>
      )}
    </main>
  );

  function renderRepositories() {
    return (
      <div>
        <div className="mb-4 flex flex-col gap-3 border-b border-stone-200 pb-4 md:flex-row md:items-center">
          <div className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" />
            <input
              type="text"
              value={repositoryQuery}
              onChange={(event) => setRepositoryQuery(event.target.value)}
              placeholder="Find a repository..."
              className="w-full rounded-md border border-stone-200 bg-white py-2 pl-9 pr-3 text-sm outline-none transition placeholder:text-stone-400 focus:border-stone-950"
            />
          </div>
          <div className="flex flex-wrap gap-2">
            {repositoryTypeFilters.map((filter) => {
              const isActive = repositoryTypeFilter === filter.id;
              return (
                <button
                  key={filter.id}
                  type="button"
                  onClick={() => setRepositoryTypeFilter(filter.id)}
                  className={clsx(
                    "inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm font-semibold transition",
                    isActive
                      ? "border-stone-950 bg-stone-950 text-white"
                      : "border-stone-200 bg-white text-stone-700 hover:border-stone-300 hover:text-stone-950"
                  )}
                >
                  {filter.label}
                  <span
                    className={clsx(
                      "rounded-full px-2 py-0.5 text-[11px]",
                      isActive ? "bg-white/12 text-white" : "bg-stone-100 text-stone-500"
                    )}
                  >
                    {filter.count}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {error ? (
          <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700" role="alert">
            {error}
          </div>
        ) : null}

        <div className="space-y-0">
          {isLoading ? (
            Array.from({ length: 5 }).map((_, index) => (
              <div key={index} className="border-b border-stone-200 bg-white/60 p-5">
                <div className="h-4 w-1/3 animate-pulse rounded bg-stone-200" />
                <div className="mt-3 h-3 w-2/3 animate-pulse rounded bg-stone-100" />
              </div>
            ))
          ) : filteredRepositoryItems.length ? (
            filteredRepositoryItems.map((item) =>
              item.kind === "lesson" ? renderLessonCard(item.lesson) : renderRepositoryCard(item.coursePackage, "list")
            )
          ) : (
            <div className="rounded-lg border border-dashed border-stone-300 bg-white/82 px-5 py-8 text-sm text-stone-500">
              没有匹配到课程或课程包。
            </div>
          )}
        </div>
      </div>
    );
  }

  function renderStars() {
    return (
      <div className="space-y-7">
        <section className="rounded-lg border border-stone-200 bg-white p-8 text-center">
          <Star className="mx-auto h-7 w-7 text-stone-400" />
          <h2 className="mt-4 text-lg font-semibold text-stone-950">Create your first list</h2>
          <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-stone-600">
            Stars 只显示你收藏的他人课程包；自己创建的课程包会保留在 Repositories。
          </p>
        </section>

        <section>
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="flex items-center gap-2 text-base font-semibold text-stone-950">
              <Bookmark className="h-4 w-4 text-amber-500" />
              Stars
            </h2>
            <span className="text-xs font-medium text-stone-500">{filteredFavoriteProjects.length} starred</span>
          </div>

          <div className="mb-4 flex flex-col gap-3 border-b border-stone-200 pb-4 md:flex-row md:items-center">
            <div className="relative min-w-0 flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" />
              <input
                type="text"
                value={starQuery}
                onChange={(event) => setStarQuery(event.target.value)}
                placeholder="Search stars"
                className="w-full rounded-md border border-stone-200 bg-white py-2 pl-9 pr-3 text-sm outline-none transition placeholder:text-stone-400 focus:border-stone-950"
              />
            </div>
            <button type="button" className="rounded-md border border-stone-200 bg-white px-3 py-2 text-sm font-semibold text-stone-700">
              Sort by: Recently starred
            </button>
          </div>

          <div className="space-y-0">
            {filteredFavoriteProjects.length ? (
              filteredFavoriteProjects.map((course) => renderStarCard(course))
            ) : (
              <div className="rounded-lg border border-dashed border-stone-300 bg-white/82 px-5 py-8 text-sm text-stone-500">
                没有匹配到已收藏的他人课程包。
              </div>
            )}
          </div>
        </section>
      </div>
    );
  }

  function renderLessonProjectMenu(lesson: Lesson) {
    const isUpdatingVisibility = busyKey === `visibility:lesson:${lesson.id}`;
    const isReviewingPublication = isUpdatingVisibility && lesson.visibility === "private";
    const isMoving = busyKey === `move:lesson:${lesson.id}`;
    const isExporting = busyKey === `export:lesson:${lesson.id}`;
    const actionBusy = busyKey !== null;

    return (
      <div
        className="absolute right-0 top-11 z-30 w-64 rounded-xl border border-stone-200 bg-white p-2 text-left shadow-[0_18px_44px_rgba(15,23,42,0.16)]"
        aria-label={`管理课程 ${lesson.title}`}
      >
        <ProjectVisibilityControl
          visibility={lesson.visibility}
          onChange={(visibility) => void handleSetLessonVisibility(lesson, visibility)}
          disabled={actionBusy}
          label="可见权限"
          ariaLabelPrefix="课程设为"
          review={lesson.publication_review.status === "not_started" ? undefined : lesson.publication_review}
          reviewing={isReviewingPublication}
        />

        <div className="my-1 h-px bg-stone-100" />

        <button
          type="button"
          onClick={() => void handleShareProject("lesson", lesson)}
          disabled={lesson.visibility !== "public" || actionBusy}
          className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-stone-700 transition hover:bg-stone-50 disabled:cursor-not-allowed disabled:text-stone-300"
          title={lesson.visibility === "public" ? "分享公开课程" : "设为 Public 后可分享"}
        >
          <Share2 className="h-4 w-4" />
          分享
        </button>
        <button
          type="button"
          onClick={() => void handleRenameLesson(lesson)}
          disabled={actionBusy}
          className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-stone-700 transition hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <PencilLine className="h-4 w-4" />
          重命名
        </button>
        <button
          type="button"
          onClick={() => void handleExportLessonPackage(lesson)}
          disabled={actionBusy}
          className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-stone-700 transition hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isExporting ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
          导出课程包
        </button>

        <div className="my-1 h-px bg-stone-100" />

        <button
          type="button"
          onClick={() => setMoveMenuLessonId((current) => (current === lesson.id ? null : lesson.id))}
          disabled={!coursePackageProjects.length || actionBusy}
          aria-expanded={moveMenuLessonId === lesson.id}
          className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-stone-700 transition hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isMoving ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <FolderClosed className="h-4 w-4" />}
          <span className="flex-1">移动到课程包</span>
          <ChevronRight
            className={clsx("h-4 w-4 text-stone-400 transition", moveMenuLessonId === lesson.id && "rotate-90")}
          />
        </button>

        {moveMenuLessonId === lesson.id ? (
          <div className="mt-1 max-h-44 space-y-1 overflow-y-auto rounded-lg bg-stone-50 p-1.5" aria-label="选择目标课程包">
            {coursePackageProjects.map((coursePackage) => (
              <button
                key={coursePackage.id}
                type="button"
                onClick={() => void handleMoveLesson(lesson, coursePackage.id)}
                disabled={actionBusy}
                className="block w-full truncate rounded-md px-2.5 py-2 text-left text-xs font-medium text-stone-700 transition hover:bg-white disabled:cursor-wait disabled:opacity-50"
              >
                {coursePackage.title}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    );
  }

  function renderPackageProjectMenu(coursePackage: CoursePackage) {
    const isUpdatingVisibility = busyKey === `visibility:package:${coursePackage.id}`;
    const isReviewingPublication = isUpdatingVisibility && coursePackage.visibility === "private";
    const actionBusy = busyKey !== null;

    return (
      <div
        className="absolute right-0 top-11 z-30 w-64 rounded-xl border border-stone-200 bg-white p-2 text-left shadow-[0_18px_44px_rgba(15,23,42,0.16)]"
        aria-label={`管理课程包 ${coursePackage.title}`}
      >
        <ProjectVisibilityControl
          visibility={coursePackage.visibility}
          onChange={(visibility) => void handleSetPackageVisibility(coursePackage, visibility)}
          disabled={actionBusy}
          label="可见权限"
          ariaLabelPrefix="课程包设为"
          review={coursePackage.publication_review.status === "not_started" ? undefined : coursePackage.publication_review}
          reviewing={isReviewingPublication}
        />

        <div className="my-1 h-px bg-stone-100" />

        <button
          type="button"
          onClick={() => void handleShareProject("package", coursePackage)}
          disabled={coursePackage.visibility !== "public" || actionBusy}
          className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-stone-700 transition hover:bg-stone-50 disabled:cursor-not-allowed disabled:text-stone-300"
          title={coursePackage.visibility === "public" ? "分享公开课程包" : "设为 Public 后可分享"}
        >
          <Share2 className="h-4 w-4" />
          分享
        </button>
        <button
          type="button"
          onClick={() => void handleRenamePackage(coursePackage)}
          disabled={actionBusy}
          className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-stone-700 transition hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <PencilLine className="h-4 w-4" />
          重命名
        </button>
      </div>
    );
  }

  function renderLessonCard(lesson: Lesson) {
    const topics = getLessonTopics(lesson);
    const isOpening = openingLessonId === lesson.id;
    const summary = lesson.summary.trim();
    const isMenuOpen = projectMenu?.kind === "lesson" && projectMenu.id === lesson.id;

    return (
      <article key={lesson.id} className="relative border-b border-stone-200 bg-white p-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => void handleOpenLesson(lesson.id)}
                className="text-left text-base font-semibold text-blue-600 hover:underline"
              >
                {lesson.title}
              </button>
              <span className="rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-[11px] font-semibold text-sky-700">
                单独课程
              </span>
              <span
                className={clsx(
                  "rounded-full border px-2 py-0.5 text-[11px] font-semibold",
                  lesson.visibility === "public"
                    ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                    : "border-stone-200 bg-stone-50 text-stone-500"
                )}
              >
                {lesson.visibility === "public" ? "Public" : "Private"}
              </span>
            </div>
            {summary ? <p className="mt-2 line-clamp-2 text-sm leading-6 text-stone-600">{summary}</p> : null}

            <div className="mt-3 flex flex-wrap gap-1.5">
              {topics.map((topic) => (
                <span
                  key={`${lesson.id}:${topic}`}
                  className="rounded-full bg-stone-100 px-2.5 py-1 text-[11px] font-semibold text-stone-600"
                >
                  {topic}
                </span>
              ))}
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-stone-500">
              <span className="inline-flex items-center gap-1">
                <BookOpen className="h-3.5 w-3.5" />
                单独课程
              </span>
              <span>{lesson.history_graph.commits.length} commits</span>
              <span>
                {ph.updated} {relFmt(lesson.updated_at)}
              </span>
            </div>
          </div>

          <div className="relative flex shrink-0 items-center gap-2" data-profile-project-menu-root>
            <button
              type="button"
              onClick={() => {
                setMoveMenuLessonId(null);
                setProjectMenu((current) =>
                  current?.kind === "lesson" && current.id === lesson.id
                    ? null
                    : { kind: "lesson", id: lesson.id }
                );
              }}
              className={clsx(
                "inline-flex h-10 w-10 items-center justify-center rounded-md border border-stone-200 bg-white text-stone-500 transition hover:border-stone-300 hover:text-stone-950",
                isMenuOpen && "border-stone-300 bg-stone-100 text-stone-950"
              )}
              aria-label={`管理课程 ${lesson.title}`}
              aria-expanded={isMenuOpen}
            >
              <MoreHorizontal className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={() => handleSelectCollaborationProject(`lesson:${lesson.id}`)}
              className="inline-flex shrink-0 items-center justify-center gap-2 rounded-md border border-violet-200 bg-violet-50 px-3 py-2 text-sm font-semibold text-violet-700 transition hover:border-violet-300 hover:bg-violet-100"
              aria-label={`打开 ${lesson.title} 的协作管理`}
            >
              <GitPullRequest className="h-4 w-4" />
              协作
            </button>
            <button
              type="button"
              onClick={() => void handleOpenLesson(lesson.id)}
              disabled={isOpening}
              className="inline-flex shrink-0 items-center justify-center gap-2 rounded-md border border-stone-200 bg-stone-50 px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:bg-white hover:text-stone-950 disabled:cursor-wait disabled:opacity-70"
            >
              {isOpening ? <LoaderCircle className="h-4 w-4 animate-spin" /> : null}
              打开
              {!isOpening ? <ArrowUpRight className="h-4 w-4" /> : null}
            </button>
            {isMenuOpen ? renderLessonProjectMenu(lesson) : null}
          </div>
        </div>
      </article>
    );
  }

  function renderRepositoryCard(coursePackage: CoursePackage, variant: "compact" | "list") {
    const topics = getPackageTopics(coursePackage);
    const updatedAt = getPackageUpdatedAt(coursePackage);
    const isCompact = variant === "compact";
    const isExpanded = !isCompact && expandedPackageIds.has(coursePackage.id);
    const lessonsListId = `package-lessons-${coursePackage.id}`;
    const isMenuOpen = projectMenu?.kind === "package" && projectMenu.id === coursePackage.id;

    return (
      <article
        key={coursePackage.id}
        className={clsx(
          "relative border-stone-200 bg-white",
          isCompact ? "min-h-32 rounded-lg border p-4" : "border-b p-5"
        )}
      >
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <Link href={`/?package=${coursePackage.id}`} className="text-base font-semibold text-blue-600 hover:underline">
                {coursePackage.title}
              </Link>
              <button
                type="button"
                onClick={() => handleTogglePackageLessons(coursePackage.id)}
                aria-expanded={isExpanded}
                aria-controls={lessonsListId}
                className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 transition hover:border-emerald-300 hover:bg-emerald-100"
              >
                <FolderClosed className="h-3 w-3" />
                课程包
                <span className="rounded-full bg-white/75 px-1.5 py-px text-[10px] text-emerald-700">
                  {coursePackage.lessons.length}
                </span>
                <ChevronDown className={clsx("h-3 w-3 transition", isExpanded ? "rotate-180" : null)} />
              </button>
              <span
                className={clsx(
                  "rounded-full border px-2 py-0.5 text-[11px] font-semibold",
                  coursePackage.visibility === "public"
                    ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                    : "border-stone-200 bg-stone-50 text-stone-500"
                )}
              >
                {coursePackage.visibility === "public" ? "Public" : "Private"}
              </span>
            </div>
            <p className="mt-2 line-clamp-2 text-sm leading-6 text-stone-600">
              {coursePackage.summary || "个人课程项目，包含课程文档、资料索引和学习活动记录。"}
            </p>

            {!isCompact ? (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {topics.map((topic) => (
                  <span
                    key={`${coursePackage.id}:${topic}`}
                    className="rounded-full bg-stone-100 px-2.5 py-1 text-[11px] font-semibold text-stone-600"
                  >
                    {topic}
                  </span>
                ))}
              </div>
            ) : null}

            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-stone-500">
              <span className="inline-flex items-center gap-1">
                <GraduationCap className="h-3.5 w-3.5" />
                {coursePackage.lessons.length} lessons
              </span>
              <span>
                {ph.updated} {updatedAt ? relFmt(updatedAt) : ph.noUpdatesYet}
              </span>
            </div>
          </div>

          <div className="relative flex shrink-0 items-center gap-2" data-profile-project-menu-root>
            <button
              type="button"
              onClick={() => {
                setMoveMenuLessonId(null);
                setProjectMenu((current) =>
                  current?.kind === "package" && current.id === coursePackage.id
                    ? null
                    : { kind: "package", id: coursePackage.id }
                );
              }}
              className={clsx(
                "inline-flex h-10 w-10 items-center justify-center rounded-md border border-stone-200 bg-white text-stone-500 transition hover:border-stone-300 hover:text-stone-950",
                isMenuOpen && "border-stone-300 bg-stone-100 text-stone-950"
              )}
              aria-label={`管理课程包 ${coursePackage.title}`}
              aria-expanded={isMenuOpen}
            >
              <MoreHorizontal className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={() => handleSelectCollaborationProject(`package:${coursePackage.id}`)}
              className="inline-flex shrink-0 items-center justify-center gap-2 rounded-md border border-violet-200 bg-violet-50 px-3 py-2 text-sm font-semibold text-violet-700 transition hover:border-violet-300 hover:bg-violet-100"
              aria-label={`打开 ${coursePackage.title} 的协作管理`}
            >
              <GitPullRequest className="h-4 w-4" />
              协作
            </button>
            <Link
              href={`/?package=${coursePackage.id}`}
              className="inline-flex shrink-0 items-center justify-center gap-2 rounded-md border border-stone-200 bg-stone-50 px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:bg-white hover:text-stone-950"
            >
              打开
              <ArrowUpRight className="h-4 w-4" />
            </Link>
            {isMenuOpen ? renderPackageProjectMenu(coursePackage) : null}
          </div>
        </div>

        {isExpanded ? (
          <ul
            id={lessonsListId}
            className="mt-4 divide-y divide-stone-200 rounded-md border border-stone-200 bg-stone-50/80"
            aria-label={`${coursePackage.title} 的单独课程列表`}
          >
            {coursePackage.lessons.length ? (
              coursePackage.lessons.map((lesson, index) => {
                const isOpening = openingLessonId === lesson.id;
                const summary = lesson.summary.trim();

                return (
                  <li
                    key={lesson.id}
                    className="flex flex-col gap-3 px-3 py-3 sm:flex-row sm:items-start sm:justify-between"
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-[11px] font-semibold text-stone-400">
                          {String(index + 1).padStart(2, "0")}
                        </span>
                        <button
                          type="button"
                          onClick={() => void handleOpenLesson(lesson.id)}
                          className="min-w-0 text-left text-sm font-semibold text-blue-600 hover:underline"
                        >
                          {lesson.title}
                        </button>
                        <span className="rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-[11px] font-semibold text-sky-700">
                          单独课程
                        </span>
                      </div>
                      {summary ? <p className="mt-1 line-clamp-1 text-xs leading-5 text-stone-500">{summary}</p> : null}
                    </div>

                    <div className="flex shrink-0 items-center gap-2">
                      <button
                        type="button"
                        onClick={() => handleSelectCollaborationProject(`lesson:${lesson.id}`)}
                        className="inline-flex items-center justify-center gap-1.5 rounded-md border border-violet-200 bg-violet-50 px-2.5 py-1.5 text-xs font-semibold text-violet-700 transition hover:border-violet-300 hover:bg-violet-100"
                        aria-label={`打开 ${lesson.title} 的协作管理`}
                      >
                        <GitPullRequest className="h-3.5 w-3.5" />
                        协作
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleOpenLesson(lesson.id)}
                        disabled={isOpening}
                        className="inline-flex items-center justify-center gap-1.5 rounded-md border border-stone-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950 disabled:cursor-wait disabled:opacity-70"
                      >
                        {isOpening ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : null}
                        打开
                        {!isOpening ? <ArrowUpRight className="h-3.5 w-3.5" /> : null}
                      </button>
                    </div>
                  </li>
                );
              })
            ) : (
              <li className="px-3 py-3 text-sm text-stone-500">这个课程包还没有单独课程。</li>
            )}
          </ul>
        ) : null}
      </article>
    );
  }

  function renderStarCard(course: (typeof OPEN_SOURCE_COURSES)[number]) {
    return (
      <article key={course.id} className="border-b border-stone-200 bg-white p-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex min-w-0 gap-3">
            <Image
              src={courseAvatarUrl(course)}
              alt=""
              className="mt-0.5 h-8 w-8 rounded-md border border-stone-200 bg-stone-100"
              width={32}
              height={32}
              unoptimized
            />
            <div className="min-w-0">
              <Link
                href={courseDetailHref(course)}
                className="block truncate text-base font-semibold text-blue-600 hover:underline"
              >
                {courseFullName(course)}
              </Link>
              <p className="mt-1 line-clamp-2 text-sm leading-6 text-stone-600">{course.summary}</p>
              <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-stone-500">
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: course.languageColor }} />
                  {course.language}
                </span>
                <span className="inline-flex items-center gap-1">
                  <Star className="h-3.5 w-3.5" />
                  {formatCompactNumber(course.stars)}
                </span>
                <span className="inline-flex items-center gap-1">
                  <GitFork className="h-3.5 w-3.5" />
                  {formatCompactNumber(course.forks)}
                </span>
                <span>
                  {ph.updated} {relFmt(course.updatedAt)}
                </span>
              </div>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            <Link
              href={courseDetailHref(course)}
              className="inline-flex items-center justify-center gap-1.5 rounded-md border border-stone-200 bg-stone-50 px-3 py-1.5 text-xs font-semibold text-stone-700 transition hover:border-stone-300 hover:bg-white hover:text-stone-950"
            >
              打开
              <ArrowUpRight className="h-3.5 w-3.5" />
            </Link>
            <button
              type="button"
              onClick={() => handleToggleCollectCourse(course.id)}
              className="inline-flex items-center justify-center gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs font-semibold text-amber-700 transition hover:border-amber-300"
            >
              <Star className="h-3.5 w-3.5 fill-current" />
              Starred
            </button>
          </div>
        </div>
      </article>
    );
  }
}
