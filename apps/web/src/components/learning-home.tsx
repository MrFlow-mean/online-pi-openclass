"use client";

import clsx from "clsx";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  Bell,
  BookOpen,
  BookText,
  Bookmark,
  Check,
  ChevronDown,
  ChevronRight,
  Code2,
  Download,
  Eye,
  Flame,
  FolderClosed,
  GitFork,
  Globe2,
  GraduationCap,
  Languages,
  Layers,
  LoaderCircle,
  LockKeyhole,
  MessageCircle,
  MoreHorizontal,
  PencilLine,
  Plus,
  Search,
  Share2,
  Star,
  Trash2,
  WalletCards,
  Upload,
} from "lucide-react";

import { AccountMenu } from "@/components/account-menu";
import { BrandMark } from "@/components/brand-mark";
import {
  HomeLessonBatchToggle,
  HomeLessonBatchToolbar,
} from "@/components/home-lesson-batch-controls";
import { InlineNameForm } from "@/components/inline-name-form";
import { LearningActivityCalendar } from "@/components/learning-activity-calendar";
import { RecentFeedCard } from "@/components/recent-feed-card";
import {
  ProjectVisibilityControl,
  PublicationReviewNotice,
} from "@/components/project-visibility-control";
import { useInterfaceLanguage } from "@/contexts/interface-language-context";
import { api } from "@/lib/api";
import {
  publicProjectHref,
  updateLessonVisibility,
  updatePackageVisibility,
  type ProjectVisibility,
} from "@/lib/project-visibility";
import { homeRelativeFormat } from "@/lib/i18n/product-ui";
import { downloadRidoc, RIDOC_FILE_ACCEPT } from "@/lib/ridoc-file";
import { useHomeLessonBatch } from "@/hooks/use-home-lesson-batch";
import {
  PROFILE_SETTINGS_CHANGED_EVENT,
  PROFILE_SETTINGS_STORAGE_KEY,
  readStoredProfileSettings,
  type InterfaceLanguage,
} from "@/lib/profile-settings-state";
import {
  DEFAULT_COLLECTED_COURSE_IDS,
  OPEN_COURSE_COLLECTION_STORAGE_KEY,
  courseAvatarUrl,
  courseDetailHref,
  courseFullName,
  formatCompactNumber,
  searchOpenCourses,
  sortOpenCourses,
  type OpenCourse,
  type OpenCourseSort,
} from "@/lib/open-courses";
import {
  FOLLOWED_UPDATE_KIND_LABELS,
  buildFollowedCourseUpdateItems,
  creatorAvatarUrl,
  type FollowedCourseUpdate,
} from "@/lib/following";
import {
  buildRecentFeed,
  type RecentFeedFilter,
} from "@/lib/recent-feed";
import type { CoursePackage, Lesson, WorkspaceState } from "@/types";

const GITHUB_REPOSITORY_URL = "https://github.com/MrFlow-mean/openclass";

type SearchFacet = { kind: "all" } | { kind: "category" | "language"; value: string };

type LessonShelfItem = {
  lesson: Lesson;
  packageId: string;
  packageTitle: string;
  isPackaged: boolean;
};

type LessonMenuState = {
  lessonId: string;
  top: number;
  left: number;
};

function countOpenCourseFacet(courses: OpenCourse[], getValue: (course: OpenCourse) => string, collatorLocale: string) {
  const counts = new Map<string, number>();

  courses.forEach((course) => {
    const value = getValue(course);
    counts.set(value, (counts.get(value) ?? 0) + 1);
  });

  return Array.from(counts, ([value, count]) => ({ value, count })).sort((left, right) => {
    if (right.count !== left.count) {
      return right.count - left.count;
    }
    return left.value.localeCompare(right.value, collatorLocale);
  });
}

function persistCollectedCourseIds(courseIds: Set<string>) {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(OPEN_COURSE_COLLECTION_STORAGE_KEY, JSON.stringify(Array.from(courseIds)));
  } catch {
    // Local storage can be unavailable in private browsing contexts.
  }
}

function sortByUpdatedAt(items: Lesson[]) {
  return [...items].sort((left, right) => {
    const leftTime = new Date(left.updated_at).getTime();
    const rightTime = new Date(right.updated_at).getTime();
    return rightTime - leftTime;
  });
}

function matchesQuery(query: string, ...values: Array<string | null | undefined>) {
  if (!query) {
    return true;
  }

  return values.some((value) => value?.toLowerCase().includes(query));
}

function followedUpdateTone(kind: FollowedCourseUpdate["updateKind"]) {
  switch (kind) {
    case "note_added":
      return "bg-sky-100 text-sky-700";
    case "course_revision":
      return "bg-rose-100 text-rose-700";
    case "new_lesson":
    default:
      return "bg-stone-100 text-stone-700";
  }
}

function followedUpdateActionLabel(kind: FollowedCourseUpdate["updateKind"]) {
  switch (kind) {
    case "note_added":
      return "published notes in";
    case "course_revision":
      return "updated";
    case "new_lesson":
    default:
      return "published";
  }
}

function followedUpdatePreviewHeading(kind: FollowedCourseUpdate["updateKind"]) {
  switch (kind) {
    case "note_added":
      return "Class Notes";
    case "course_revision":
      return "What's Changed";
    case "new_lesson":
    default:
      return "Highlights";
  }
}

export function LearningHome() {
  const router = useRouter();
  const { language, texts: txt, intlLocale } = useInterfaceLanguage();
  const h = txt.home;
  const errMsgs = useRef(h);

  useEffect(() => {
    errMsgs.current = h;
  });

  const homeRelFmt = useMemo(
    () => (value: string | Date | null | undefined) => homeRelativeFormat(value, txt.homeRelative, txt.lang),
    [txt]
  );

  const [workspaceState, setWorkspaceState] = useState<WorkspaceState | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const deferredQuery = useDeferredValue(searchQuery.trim().toLowerCase());
  const [openCourseSort, setOpenCourseSort] = useState<OpenCourseSort>("best-match");
  const [openCourseFacet, setOpenCourseFacet] = useState<SearchFacet>({ kind: "all" });
  const [collectedCourseIds, setCollectedCourseIds] = useState<Set<string>>(
    () => new Set(DEFAULT_COLLECTED_COURSE_IDS)
  );
  const [feedFilter, setFeedFilter] = useState<RecentFeedFilter>("all");
  const [feedCollapsed, setFeedCollapsed] = useState(true);
  const [notificationOpen, setNotificationOpen] = useState(false);
  const [selectedPackageId, setSelectedPackageId] = useState<string | null>(null);
  const [selectedLessonId, setSelectedLessonId] = useState<string | null>(null);
  const [packageLessonsExpanded, setPackageLessonsExpanded] = useState(true);
  const [coursePackagesCollapsed, setCoursePackagesCollapsed] = useState(false);
  const [standaloneLessonsCollapsed, setStandaloneLessonsCollapsed] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [lessonMenuState, setLessonMenuState] = useState<LessonMenuState | null>(null);
  const [lessonMoveMenuState, setLessonMoveMenuState] = useState<LessonMenuState | null>(null);
  const [isCreatingPackageInline, setIsCreatingPackageInline] = useState(false);
  const [standaloneCreateMenuOpen, setStandaloneCreateMenuOpen] = useState(false);
  const ridocFileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    let isDisposed = false;

    async function load() {
      try {
        const payload = await api.getWorkspace();
        if (isDisposed) {
          return;
        }
        setWorkspaceState(payload);
        if (typeof window !== "undefined") {
          const packageIdFromUrl = new URLSearchParams(window.location.search).get("package");
          const standalonePackageId = payload.packages.find((packageItem) => packageItem.is_standalone)?.id ?? payload.packages[0]?.id;
          if (
            packageIdFromUrl &&
            packageIdFromUrl !== standalonePackageId &&
            payload.packages.some((packageItem) => packageItem.id === packageIdFromUrl)
          ) {
            setSelectedPackageId(packageIdFromUrl);
            setPackageLessonsExpanded(true);
          }
        }
        setError(null);
      } catch (loadError) {
        if (isDisposed) {
          return;
        }
        setError(loadError instanceof Error ? loadError.message : errMsgs.current.loadError);
      } finally {
        if (!isDisposed) {
          setIsLoading(false);
        }
      }
    }

    void load();

    return () => {
      isDisposed = true;
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
    function handlePointerDown(event: MouseEvent) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) {
        return;
      }
      if (!target.closest("[data-standalone-create-menu-root]")) {
        setStandaloneCreateMenuOpen(false);
      }
      if (target.closest("[data-lesson-menu-root]")) {
        return;
      }
      setLessonMenuState(null);
      setLessonMoveMenuState(null);
      if (!target.closest("[data-package-selection-root]")) {
        setSelectedPackageId(null);
        setPackageLessonsExpanded(false);
      }
      if (!target.closest("[data-lesson-selection-root]")) {
        setSelectedLessonId(null);
      }
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") {
        return;
      }
      setLessonMenuState(null);
      setLessonMoveMenuState(null);
      setIsCreatingPackageInline(false);
      setStandaloneCreateMenuOpen(false);
      setSelectedPackageId(null);
      setSelectedLessonId(null);
      setPackageLessonsExpanded(false);
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, []);

  const packages = workspaceState?.packages ?? [];
  const standalonePackage = packages.find((packageItem) => packageItem.is_standalone) ?? packages[0] ?? null;
  const coursePackages = packages.filter((packageItem) => packageItem.id !== standalonePackage?.id);
  const selectedCoursePackage = selectedPackageId
    ? coursePackages.find((item) => item.id === selectedPackageId) ?? null
    : null;
  const movablePackages = coursePackages;
  const feedLessons = packages.flatMap((packageItem) =>
    packageItem.lessons.map((lesson) => ({
      lesson,
      packageId: packageItem.id,
      packageTitle: packageItem.title,
      isStandalone: packageItem.id === standalonePackage?.id,
    }))
  );
  const selectedPackageLessons = sortByUpdatedAt(selectedCoursePackage?.lessons ?? []);
  const selectedPackageActiveLesson = selectedLessonId
    ? selectedCoursePackage?.lessons.find((lesson) => lesson.id === selectedLessonId) ?? null
    : null;
  const standaloneLessonItems: LessonShelfItem[] = sortByUpdatedAt(standalonePackage?.lessons ?? []).map((lesson) => ({
    lesson,
    packageId: standalonePackage?.id ?? "standalone",
    packageTitle: standalonePackage?.title ?? h.standaloneFallbackTitle,
    isPackaged: false,
  }));
  const filteredLessonItems = standaloneLessonItems.filter(({ lesson, packageTitle }) =>
    matchesQuery(
      deferredQuery,
      lesson.title,
      lesson.summary,
      lesson.tags.join(" "),
      lesson.board_document.title,
      lesson.board_document.content_text,
      packageTitle
    )
  );
  const lessonBatch = useHomeLessonBatch({
    availableLessonIds: standaloneLessonItems.map(({ lesson }) => lesson.id),
    visibleLessonIds: filteredLessonItems.map(({ lesson }) => lesson.id),
    onWorkspaceChange: (workspace) => {
      setWorkspaceState(workspace);
      setSelectedLessonId(null);
      setError(null);
    },
    onError: setError,
    moveErrorMessage: h.batchMoveFail,
    deleteErrorMessage: h.batchDeleteFail,
    deleteConfirmMessage: h.batchDeleteConfirm,
  });

  const matchingOpenCourses = useMemo(() => searchOpenCourses(deferredQuery), [deferredQuery]);
  const categoryFacetCounts = useMemo(
    () => countOpenCourseFacet(matchingOpenCourses, (course) => course.category, intlLocale),
    [matchingOpenCourses, intlLocale]
  );
  const languageFacetCounts = useMemo(
    () => countOpenCourseFacet(matchingOpenCourses, (course) => course.language, intlLocale),
    [matchingOpenCourses, intlLocale]
  );
  const openCourseResults = useMemo(() => {
    const facetedCourses = matchingOpenCourses.filter((course) => {
      if (openCourseFacet.kind === "all") {
        return true;
      }
      if (openCourseFacet.kind === "category") {
        return course.category === openCourseFacet.value;
      }
      return course.language === openCourseFacet.value;
    });

    return sortOpenCourses(facetedCourses, openCourseSort);
  }, [matchingOpenCourses, openCourseFacet, openCourseSort]);
  const collectedOpenCourseCount = collectedCourseIds.size;

  const lessonMenuLesson =
    lessonMenuState ? standaloneLessonItems.find(({ lesson }) => lesson.id === lessonMenuState.lessonId)?.lesson ?? null : null;
  const feedItems = buildRecentFeed(feedLessons);
  const visibleFeedItems = feedFilter === "all" ? feedItems : feedItems.filter((item) => item.kind === feedFilter);
  const followedProjectUpdates = useMemo(() => buildFollowedCourseUpdateItems(), []);
  const followingUnreadCount = followedProjectUpdates.length;
  const followingBadge = followingUnreadCount > 99 ? "99+" : followingUnreadCount.toString();
  const notificationUpdates = followedProjectUpdates.slice(0, 4);
  const quickLinksLabel = language === "en" ? "Project links" : "项目链接";
  const githubLinkLabel = language === "en" ? "Open GitHub repository" : "打开 GitHub 仓库";
  const projectDocsLinkLabel = language === "en" ? "Open project documentation" : "打开项目文档";

  async function handleOpenLesson(lessonId: string) {
    setSelectedLessonId(lessonId);
    setBusyKey(`lesson:${lessonId}`);
    setLessonMenuState(null);
    setLessonMoveMenuState(null);

    try {
      await api.openLesson(lessonId);
      router.push("/studio");
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : errMsgs.current.lessonOpenError);
    } finally {
      setBusyKey(null);
    }
  }

  async function handleOpenStandaloneWorkspace() {
    setStandaloneCreateMenuOpen(false);
    if (!standalonePackage) {
      router.push("/studio");
      return;
    }

    setBusyKey(`package:${standalonePackage.id}`);
    setSelectedPackageId(null);
    setSelectedLessonId(null);
    setPackageLessonsExpanded(false);
    setLessonMenuState(null);
    setLessonMoveMenuState(null);
    try {
      await api.openPackage(standalonePackage.id);
      const createdPackage = await api.generateLesson({
        targetPackageId: standalonePackage.id,
        startBlank: true,
      });
      const payload = await api.getWorkspace();
      setWorkspaceState(payload);
      setSelectedLessonId(createdPackage.active_lesson_id ?? null);
      setError(null);
      router.push("/studio");
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : errMsgs.current.openStandaloneFail);
    } finally {
      setBusyKey(null);
    }
  }

  async function handleMoveLesson(lesson: Lesson, targetPackageId: string) {
    if (!targetPackageId) {
      return;
    }

    setBusyKey(`move:${lesson.id}`);
    setLessonMenuState(null);
    setLessonMoveMenuState(null);
    try {
      const payload = await api.moveLesson(lesson.id, targetPackageId);
      setWorkspaceState(payload);
      setError(null);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : errMsgs.current.moveLessonFail);
    } finally {
      setBusyKey(null);
    }
  }

  async function handleRenameLesson(lesson: Lesson) {
    const nextTitle = window.prompt(h.renameLessonPrompt, lesson.title);
    if (!nextTitle?.trim() || nextTitle.trim() === lesson.title) {
      return;
    }

    setBusyKey(`rename:${lesson.id}`);
    setLessonMenuState(null);
    setLessonMoveMenuState(null);
    try {
      const payload = await api.renameLesson(lesson.id, nextTitle.trim());
      setWorkspaceState(payload);
      setError(null);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : errMsgs.current.renameLessonFail);
    } finally {
      setBusyKey(null);
    }
  }

  async function handleDeleteLesson(lesson: Lesson) {
    if (typeof window !== "undefined" && !window.confirm(`确定删除《${lesson.title}》吗？`)) {
      return;
    }

    setBusyKey(`delete:${lesson.id}`);
    setLessonMenuState(null);
    setLessonMoveMenuState(null);
    try {
      const payload = await api.deleteLesson(lesson.id);
      setWorkspaceState(payload);
      setError(null);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : errMsgs.current.deleteLessonFail);
    } finally {
      setBusyKey(null);
    }
  }

  async function handleExportLessonPackage(lesson: Lesson) {
    setBusyKey(`ridoc:export:${lesson.id}`);
    setLessonMenuState(null);
    setLessonMoveMenuState(null);
    try {
      const blob = await api.exportRidoc(lesson.id);
      downloadRidoc(blob, lesson);
      setError(null);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : errMsgs.current.exportLessonPackageFail);
    } finally {
      setBusyKey(null);
    }
  }

  async function handleSetLessonVisibility(lesson: Lesson, visibility: ProjectVisibility) {
    setBusyKey(`visibility:lesson:${lesson.id}`);
    try {
      const payload = await updateLessonVisibility(lesson.id, visibility);
      setWorkspaceState(payload);
      const updatedLesson = payload.packages
        .flatMap((packageItem) => packageItem.lessons)
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

  async function handleShareLesson(lesson: Lesson) {
    if (typeof window === "undefined" || lesson.visibility !== "public") {
      return;
    }
    const url = new URL(publicProjectHref("lesson", lesson.id), window.location.origin).toString();
    try {
      if (typeof navigator.share === "function") {
        await navigator.share({ title: lesson.title, text: `分享课程：${lesson.title}`, url });
        return;
      }
      window.prompt("复制公开课程链接", url);
    } catch (shareError) {
      if (!(shareError instanceof DOMException && shareError.name === "AbortError")) {
        setError(shareError instanceof Error ? shareError.message : errMsgs.current.sharePackageFail);
      }
    }
  }

  async function handleLoadLessonPackage(file: File) {
    setBusyKey("ridoc:import");
    setStandaloneCreateMenuOpen(false);
    setLessonMenuState(null);
    setLessonMoveMenuState(null);
    try {
      const importedPackage = await api.importRidoc(file);
      const payload = await api.getWorkspace();
      const importedWorkspacePackage = payload.packages.find((item) => item.id === importedPackage.id) ?? null;
      setWorkspaceState(payload);
      setSelectedLessonId(null);
      if (importedWorkspacePackage?.is_standalone) {
        setSelectedPackageId(null);
        setStandaloneLessonsCollapsed(false);
      } else {
        setSelectedPackageId(importedPackage.id);
        setCoursePackagesCollapsed(false);
        setPackageLessonsExpanded(true);
      }
      setError(null);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : errMsgs.current.loadLessonPackageFail);
    } finally {
      setBusyKey(null);
    }
  }

  async function handleCreatePackage(title: string) {
    const trimmedTitle = title.trim();
    if (!trimmedTitle) {
      return;
    }

    setBusyKey("package:create");
    try {
      const payload = await api.createPackage(trimmedTitle);
      setWorkspaceState(payload);
      setSelectedPackageId(payload.active_package_id ?? null);
      setPackageLessonsExpanded(true);
      setIsCreatingPackageInline(false);
      setError(null);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : errMsgs.current.createPackageFail);
    } finally {
      setBusyKey(null);
    }
  }

  async function handleOpenPackage(packageItem: CoursePackage) {
    if (packageItem.id === selectedPackageId) {
      setSelectedPackageId(null);
      setSelectedLessonId(null);
      setPackageLessonsExpanded(false);
      return;
    }

    setSelectedPackageId(packageItem.id);
    setSelectedLessonId(null);
    setPackageLessonsExpanded(true);
    setBusyKey(`package:${packageItem.id}`);

    try {
      const payload = await api.openPackage(packageItem.id);
      setWorkspaceState(payload);
      setError(null);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : errMsgs.current.openPackageFail);
    } finally {
      setBusyKey(null);
    }
  }

  async function handleRenameSelectedPackage() {
    if (!selectedCoursePackage) {
      return;
    }

    const nextTitle = window.prompt("请输入新的课程包名称", selectedCoursePackage.title);
    if (!nextTitle?.trim() || nextTitle.trim() === selectedCoursePackage.title) {
      return;
    }

    setBusyKey(`package:rename:${selectedCoursePackage.id}`);
    try {
      const payload = await api.renamePackage(selectedCoursePackage.id, nextTitle.trim());
      setWorkspaceState(payload);
      setError(null);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : errMsgs.current.renamePackageFail);
    } finally {
      setBusyKey(null);
    }
  }

  async function handleDeleteSelectedPackage() {
    if (!selectedCoursePackage) {
      return;
    }

    const lessonCount = selectedCoursePackage.lessons.length;
    const message = lessonCount
      ? `确定删除《${selectedCoursePackage.title}》吗？包内 ${lessonCount} 节单课也会一起删除。`
      : `确定删除《${selectedCoursePackage.title}》吗？`;
    if (typeof window !== "undefined" && !window.confirm(message)) {
      return;
    }

    setBusyKey(`package:delete:${selectedCoursePackage.id}`);
    try {
      const payload = await api.deletePackage(selectedCoursePackage.id);
      setWorkspaceState(payload);
      setSelectedPackageId(null);
      setSelectedLessonId(null);
      setPackageLessonsExpanded(false);
      setError(null);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : errMsgs.current.deletePackageFail);
    } finally {
      setBusyKey(null);
    }
  }

  async function handleSetSelectedPackageVisibility(visibility: ProjectVisibility) {
    if (!selectedCoursePackage) {
      return;
    }
    setBusyKey(`visibility:package:${selectedCoursePackage.id}`);
    try {
      const payload = await updatePackageVisibility(selectedCoursePackage.id, visibility);
      setWorkspaceState(payload);
      const updatedPackage = payload.packages.find((item) => item.id === selectedCoursePackage.id);
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

  async function handleShareSelectedPackage() {
    if (!selectedCoursePackage || selectedCoursePackage.visibility !== "public" || typeof window === "undefined") {
      return;
    }

    const shareUrl = new URL(publicProjectHref("package", selectedCoursePackage.id), window.location.origin);
    const shareData = {
      title: selectedCoursePackage.title,
      text: `分享课程包：${selectedCoursePackage.title}`,
      url: shareUrl.toString(),
    };

    try {
      if (typeof navigator.share === "function") {
        await navigator.share(shareData);
        return;
      }
      window.prompt("复制课程包链接", shareData.url);
    } catch (shareError) {
      if (shareError instanceof DOMException && shareError.name === "AbortError") {
        return;
      }
      setError(shareError instanceof Error ? shareError.message : errMsgs.current.sharePackageFail);
    }
  }

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

  function handleToggleInterfaceLanguage() {
    if (typeof window === "undefined") {
      return;
    }

    const nextLanguage: InterfaceLanguage = language === "en" ? "zh-CN" : "en";
    const nextSettings = {
      ...readStoredProfileSettings(),
      interfaceLanguage: nextLanguage,
    };

    try {
      window.localStorage.setItem(PROFILE_SETTINGS_STORAGE_KEY, JSON.stringify(nextSettings));
      window.dispatchEvent(new CustomEvent(PROFILE_SETTINGS_CHANGED_EVENT, { detail: nextSettings }));
    } catch {
      // Browser storage can be unavailable in private browsing contexts.
    }
  }

  const feedFilters = useMemo(
    () => [
      { id: "all" as const, label: h.filterAll },
      { id: "commit" as const, label: h.filterMine },
    ],
    [h]
  );

  return (
    <div className="relative min-h-screen overflow-hidden bg-[#f7f5ef] text-[#171717]">
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute left-[-7rem] top-16 h-64 w-64 rounded-full bg-sky-200/45 blur-3xl" />
        <div className="absolute right-[-5rem] top-0 h-80 w-80 rounded-full bg-orange-200/40 blur-3xl" />
        <div className="absolute bottom-[-8rem] right-32 h-72 w-72 rounded-full bg-emerald-200/30 blur-3xl" />
      </div>

      <div className="relative flex min-h-screen w-full flex-col lg:flex-row">
        <aside className="relative z-[90] h-[100dvh] border-b border-stone-200/80 bg-[#fcfbf8]/85 backdrop-blur lg:fixed lg:left-0 lg:top-0 lg:h-screen lg:w-80 lg:border-b-0 lg:border-r">
          <div className="flex h-full min-h-0 flex-col p-4 sm:p-5">
            <div className="mb-8 flex items-center justify-between gap-4 px-2">
              <div className="flex min-w-0 items-center gap-3">
                <BrandMark
                  alt=""
                  className="h-11 w-11 rounded-xl border border-stone-200 bg-white shadow-sm"
                  priority
                  size={88}
                />
                <div className="min-w-0">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-stone-400">{h.brandSubtitle}</p>
                  <h1 className="mt-1 truncate text-2xl font-semibold tracking-tight text-stone-950">{h.brandTitle}</h1>
                </div>
              </div>
            </div>

            <div className="mb-6 shrink-0">
              <div className="mb-3 flex items-center justify-between px-2">
                <h2 className="text-[11px] font-semibold uppercase tracking-[0.24em] text-stone-400">{h.coursePackages}</h2>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => {
                      setCoursePackagesCollapsed((current) => !current);
                      setSelectedPackageId(null);
                      setSelectedLessonId(null);
                      setPackageLessonsExpanded(false);
                    }}
                    className="flex h-8 w-8 items-center justify-center rounded-full text-stone-400 transition hover:bg-stone-100 hover:text-stone-950"
                    aria-label={coursePackagesCollapsed ? h.expandCoursePackages : h.collapseCoursePackages}
                    aria-expanded={!coursePackagesCollapsed}
                    aria-controls="learning-home-course-packages"
                    title={coursePackagesCollapsed ? h.expandCoursePackages : h.collapseCoursePackages}
                  >
                    <ChevronDown
                      className={clsx("h-4 w-4 transition-transform duration-200", coursePackagesCollapsed && "-rotate-90")}
                    />
                  </button>
                  <button
                    type="button"
                    onClick={() => setIsCreatingPackageInline(true)}
                    className="flex h-8 w-8 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 shadow-sm transition hover:border-stone-300 hover:bg-stone-50 hover:text-stone-950"
                    aria-label={h.addPackageAria}
                  >
                    {busyKey === "package:create" ? (
                      <LoaderCircle className="h-4 w-4 animate-spin" />
                    ) : (
                      <Plus className="h-4 w-4" strokeWidth={2.2} />
                    )}
                  </button>
                </div>
              </div>
              <div
                id="learning-home-course-packages"
                className={clsx(
                  "custom-scrollbar max-h-[46dvh] space-y-2 overflow-y-auto overscroll-contain pr-1",
                  coursePackagesCollapsed && "hidden"
                )}
              >
                {coursePackages.length ? (
                  coursePackages.map((packageItem) => {
                      const isActive = packageItem.id === selectedPackageId;
                      const isBusy = busyKey === `package:${packageItem.id}`;
                      return (
                      <div key={packageItem.id} className="relative" data-package-selection-root>
                        <button
                          type="button"
                          onClick={() => void handleOpenPackage(packageItem)}
                          className={clsx(
                            "group w-full rounded-2xl border px-4 py-3 text-left transition",
                            isActive
                              ? "border-stone-950 bg-stone-950 text-white shadow-[0_18px_35px_rgba(23,23,23,0.14)]"
                              : "border-transparent bg-white/75 text-stone-700 hover:border-stone-200 hover:bg-white"
                          )}
                        >
                          <div className="flex items-start gap-3">
                            <div
                              className={clsx(
                                "mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl border transition",
                                isActive
                                  ? "border-white/10 bg-white/10 text-white"
                                  : "border-stone-200 bg-stone-100 text-stone-500 group-hover:text-stone-950"
                              )}
                            >
                              {isBusy ? (
                                <LoaderCircle className="h-4 w-4 animate-spin" />
                              ) : (
                                <FolderClosed className="h-4 w-4" />
                              )}
                            </div>
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center justify-between gap-2">
                                <p className="truncate text-sm font-semibold">{packageItem.title}</p>
                                <span className="flex shrink-0 items-center gap-1">
                                  <span
                                    className={clsx(
                                      "rounded-full px-2 py-0.5 text-[10px] font-semibold",
                                      isActive ? "bg-white/10 text-white" : "bg-stone-100 text-stone-500"
                                    )}
                                  >
                                    {packageItem.lessons.length}
                                  </span>
                                  {isActive ? (
                                    <ChevronDown
                                      className={clsx(
                                        "h-3.5 w-3.5 transition-transform",
                                        packageLessonsExpanded ? "rotate-180" : "rotate-0"
                                      )}
                                    />
                                  ) : null}
                                </span>
                              </div>
                              <p
                                className={clsx(
                                  "mt-1 line-clamp-2 text-xs leading-5",
                                  isActive ? "text-white/75" : "text-stone-500"
                                )}
                              >
                                {isActive
                                  ? packageLessonsExpanded
                                    ? h.packageSelectedExpanded
                                    : h.packageSelectedCollapsed
                                  : packageItem.summary || h.emptyPackage}
                              </p>
                            </div>
                          </div>
                        </button>

                        {isActive && packageLessonsExpanded && selectedCoursePackage ? (
                          <>
                            <div className="mt-3 lg:hidden">{renderSelectedPackagePanel()}</div>
                            <div className="absolute left-[calc(100%+0.875rem)] top-0 z-[120] hidden w-80 lg:block">
                              {renderSelectedPackagePanel()}
                            </div>
                          </>
                        ) : null}
                      </div>
                      );
                    })
                ) : isCreatingPackageInline ? null : (
                  <div className="rounded-2xl border border-dashed border-stone-300 bg-white/70 px-4 py-6 text-sm text-stone-500">
                    {h.noPackages}
                  </div>
                )}
                {isCreatingPackageInline ? (
                  <div data-package-selection-root>
                    <InlineNameForm
                      label={h.packageNameLabel}
                      placeholder={h.packageNamePlaceholder}
                      isBusy={busyKey === "package:create"}
                      onCancel={() => setIsCreatingPackageInline(false)}
                      onSubmit={handleCreatePackage}
                    />
                  </div>
                ) : null}
              </div>
            </div>

            <div className="flex min-h-0 flex-1 flex-col">
              <div className="mb-3 flex items-center justify-between px-2">
                <h2 className="text-[11px] font-semibold uppercase tracking-[0.24em] text-stone-400">{h.standaloneLessons}</h2>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setStandaloneLessonsCollapsed((current) => !current)}
                    className="flex h-8 w-8 items-center justify-center rounded-full text-stone-400 transition hover:bg-stone-100 hover:text-stone-950"
                    aria-label={standaloneLessonsCollapsed ? h.expandStandaloneLessons : h.collapseStandaloneLessons}
                    aria-expanded={!standaloneLessonsCollapsed}
                    aria-controls="learning-home-standalone-lessons"
                    title={standaloneLessonsCollapsed ? h.expandStandaloneLessons : h.collapseStandaloneLessons}
                  >
                    <ChevronDown
                      className={clsx("h-4 w-4 transition-transform duration-200", standaloneLessonsCollapsed && "-rotate-90")}
                    />
                  </button>
                  <HomeLessonBatchToggle
                    isActive={lessonBatch.isActive}
                    disabled={isLoading || !standaloneLessonItems.length || lessonBatch.isBusy}
                    manageLabel={h.batchManage}
                    cancelLabel={h.batchCancel}
                    onStart={() => {
                      setLessonMenuState(null);
                      setLessonMoveMenuState(null);
                      setSelectedLessonId(null);
                      setStandaloneLessonsCollapsed(false);
                      lessonBatch.start();
                    }}
                    onCancel={lessonBatch.cancel}
                  />
                  <div className="relative" data-standalone-create-menu-root>
                    <button
                      type="button"
                      onClick={() => {
                        setLessonMenuState(null);
                        setLessonMoveMenuState(null);
                        setStandaloneCreateMenuOpen((current) => !current);
                      }}
                      disabled={busyKey !== null}
                      className={clsx(
                        "rounded-xl p-1.5 text-stone-400 transition hover:bg-stone-200/60 hover:text-stone-950",
                        standaloneCreateMenuOpen && "bg-stone-200/60 text-stone-950"
                      )}
                      aria-label={h.standaloneCreateMenuAria}
                      aria-haspopup="menu"
                      aria-expanded={standaloneCreateMenuOpen}
                    >
                      {standalonePackage && busyKey === `package:${standalonePackage.id}` ? (
                        <LoaderCircle className="h-4 w-4 animate-spin" />
                      ) : (
                        <Plus className="h-4 w-4" />
                      )}
                    </button>

                    {standaloneCreateMenuOpen ? (
                      <div
                        role="menu"
                        className="absolute right-0 top-full z-[130] mt-2 w-44 rounded-[18px] border border-stone-200 bg-white p-2 shadow-[0_18px_40px_rgba(15,23,42,0.14)]"
                      >
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => {
                            setStandaloneCreateMenuOpen(false);
                            ridocFileInputRef.current?.click();
                          }}
                          className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm text-stone-700 transition hover:bg-stone-50"
                        >
                          <Upload className="h-4 w-4" />
                          {h.importCourseFile}
                        </button>
                        <button
                          type="button"
                          role="menuitem"
                          onClick={() => void handleOpenStandaloneWorkspace()}
                          className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm text-stone-700 transition hover:bg-stone-50"
                        >
                          <Plus className="h-4 w-4" />
                          {h.createCourse}
                        </button>
                      </div>
                    ) : null}
                  </div>
                </div>
              </div>

              {lessonBatch.isActive ? (
                <HomeLessonBatchToolbar
                  selectedCount={lessonBatch.selectedCount}
                  allVisibleSelected={lessonBatch.allVisibleSelected}
                  targetPackageId={lessonBatch.targetPackageId}
                  packages={movablePackages}
                  action={lessonBatch.action}
                  selectedLabel={h.batchSelected}
                  selectAllLabel={h.batchSelectAll}
                  clearLabel={h.batchClear}
                  choosePackageLabel={h.batchChoosePackage}
                  moveLabel={h.batchMove}
                  deleteLabel={h.batchDelete}
                  onToggleAll={lessonBatch.toggleAllVisible}
                  onClear={lessonBatch.clearSelection}
                  onTargetPackageChange={lessonBatch.setTargetPackageId}
                  onMove={() => void lessonBatch.moveSelected()}
                  onDelete={() => void lessonBatch.deleteSelected()}
                />
              ) : null}

              <div
                id="learning-home-standalone-lessons"
                className={clsx(
                  "custom-scrollbar min-h-0 flex-1 space-y-2 overflow-y-auto pb-4 pr-1",
                  standaloneLessonsCollapsed && "hidden"
                )}
              >
                {isLoading ? (
                  Array.from({ length: 4 }).map((_, index) => (
                    <div key={index} className="rounded-2xl border border-stone-200 bg-white px-4 py-4">
                      <div className="h-4 w-2/3 animate-pulse rounded bg-stone-200" />
                      <div className="mt-3 h-3 w-full animate-pulse rounded bg-stone-100" />
                    </div>
                  ))
                ) : filteredLessonItems.length ? (
                  filteredLessonItems.map(({ lesson }) => {
                    const isLessonActive = lesson.id === selectedLessonId;
                    const isBatchSelected = lessonBatch.selectedLessonIds.has(lesson.id);
                    const buttonBusy = busyKey === `lesson:${lesson.id}`;
                    const isMenuOpen = lessonMenuState?.lessonId === lesson.id;
                    return (
                      <article
                        key={lesson.id}
                        data-lesson-selection-root
                        className={clsx(
                          "relative rounded-2xl border bg-white transition",
                          isBatchSelected || isLessonActive
                            ? "border-stone-950 shadow-[0_12px_30px_rgba(0,0,0,0.06)]"
                            : "border-transparent bg-white/65 hover:border-stone-200 hover:bg-white"
                        )}
                      >
                        {!lessonBatch.isActive ? (
                          <div className="absolute right-2 top-2 z-20" data-lesson-menu-root>
                            <button
                              type="button"
                              onClick={(event) => {
                                event.stopPropagation();
                                const rect = event.currentTarget.getBoundingClientRect();
                                setLessonMoveMenuState(null);
                                setLessonMenuState((current) =>
                                  current?.lessonId === lesson.id
                                    ? null
                                    : {
                                        lessonId: lesson.id,
                                        top: rect.bottom + 6,
                                        left: Math.max(16, rect.right - 224),
                                      }
                                );
                              }}
                              className={clsx(
                                "flex h-8 w-8 items-center justify-center rounded-lg text-stone-400 transition hover:bg-stone-100 hover:text-stone-950",
                                isMenuOpen && "bg-stone-100 text-stone-950"
                              )}
                              aria-label={h.lessonMenuAria}
                              title={h.lessonMoreTitle}
                            >
                              <MoreHorizontal className="h-4 w-4" />
                            </button>
                          </div>
                        ) : null}

                        <button
                          type="button"
                          onClick={() =>
                            lessonBatch.isActive
                              ? lessonBatch.toggleLesson(lesson.id)
                              : void handleOpenLesson(lesson.id)
                          }
                          className={clsx(
                            "group w-full px-4 py-3 text-left",
                            lessonBatch.isActive ? "pr-4" : "pr-14"
                          )}
                          aria-pressed={lessonBatch.isActive ? isBatchSelected : undefined}
                        >
                          <div className="flex items-start gap-3">
                            <div
                              className={clsx(
                                "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border",
                                isBatchSelected || isLessonActive
                                  ? "border-stone-950 bg-stone-950 text-white"
                                  : "border-stone-200 bg-stone-100 text-stone-500 group-hover:text-stone-950"
                              )}
                            >
                              {lessonBatch.isActive ? (
                                <Check className={clsx("h-4 w-4", !isBatchSelected && "opacity-0")} />
                              ) : buttonBusy ? (
                                <LoaderCircle className="h-4 w-4 animate-spin" />
                              ) : (
                                <BookText className="h-4 w-4" />
                              )}
                            </div>
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center justify-between gap-3">
                                <p className="truncate text-sm font-medium text-stone-950">{lesson.title}</p>
                                <span className="flex shrink-0 items-center gap-1.5 text-[10px] text-stone-400">
                                  {lesson.visibility === "public" ? (
                                    <Globe2 className="h-3 w-3 text-emerald-600" aria-label="Public" />
                                  ) : (
                                    <LockKeyhole className="h-3 w-3" aria-label="Private" />
                                  )}
                                  {homeRelFmt(lesson.updated_at)}
                                </span>
                              </div>
                              <p className="mt-1 line-clamp-2 text-xs leading-5 text-stone-500">{lesson.summary}</p>
                            </div>
                          </div>
                        </button>
                      </article>
                    );
                  })
                ) : (
                  <div className="rounded-2xl border border-dashed border-stone-300 bg-white/70 px-4 py-6 text-sm text-stone-500">
                    {standaloneLessonItems.length ? h.noLessonMatch : h.noStandalone}
                  </div>
                )}
              </div>
            </div>

          </div>
        </aside>

        <main className="relative flex-1 px-4 py-5 sm:px-6 lg:ml-80 lg:px-8 xl:pr-[25rem]">
          <div className="mx-auto max-w-4xl">
            {error ? (
              <div className="mb-6 rounded-[24px] border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700">
                {error}
              </div>
            ) : null}

            <section className="mb-12">
              <div className="relative">
                <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-4 text-stone-400">
                  <Search className="h-4 w-4" />
                </div>
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder={h.searchPlaceholder}
                  className="w-full rounded-[28px] border border-white/70 bg-white/80 py-4 pl-11 pr-24 text-sm text-stone-950 shadow-[0_18px_40px_rgba(15,23,42,0.06)] outline-none transition placeholder:text-stone-400 focus:border-stone-950 focus:bg-white"
                />
                <div className="pointer-events-none absolute inset-y-0 right-4 flex items-center">
                  <span className="rounded-md border border-stone-200 bg-white px-2 py-1 font-mono text-[10px] text-stone-400">
                    ⌘K
                  </span>
                </div>
              </div>
            </section>

            {deferredQuery ? (
              renderOpenCourseSearchResults()
            ) : (
              <>
            <LearningActivityCalendar
              workspace={workspaceState}
              labels={{
                title: h.activityTitle,
                subtitle: h.activitySubtitle,
                total: h.activityTotal,
                dayTitle: h.activityDayTitle,
                lastActivePrefix: h.lastActivePrefix,
                noActivityYet: h.noActivityYet,
              }}
              formatRelativeDate={homeRelFmt}
            />

            <section className="mb-12">
              <div className="rounded-[30px] border border-white/70 bg-[linear-gradient(180deg,#ffffff_0%,#faf8f2_100%)] p-6 shadow-[0_18px_50px_rgba(15,23,42,0.07)] sm:p-7">
                <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <div className="flex items-center gap-2">
                      <h3 className="flex items-center gap-2 text-lg font-semibold text-stone-950">
                        <Activity className="h-5 w-5" />
                        Feed
                      </h3>
                      <button
                        type="button"
                        onClick={() => setFeedCollapsed((current) => !current)}
                        className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-600 transition hover:border-stone-300 hover:text-stone-950"
                        aria-label={feedCollapsed ? h.feedExpandAria : h.feedCollapseAria}
                        aria-expanded={!feedCollapsed}
                        aria-controls="learning-home-feed-content"
                        title={feedCollapsed ? h.feedExpandAria : h.feedCollapseAria}
                      >
                        <ChevronDown
                          className={clsx(
                            "h-4 w-4 transition-transform duration-200",
                            feedCollapsed ? "rotate-0" : "rotate-180"
                          )}
                        />
                      </button>
                    </div>
                    <p className="mt-1 text-sm text-stone-500">
                      {h.feedSubtitle}
                    </p>
                  </div>

                  {feedCollapsed ? null : (
                    <div className="flex flex-wrap items-center gap-2">
                      {feedFilters.map((filter) => {
                        const isActive = feedFilter === filter.id;
                        return (
                          <button
                            key={filter.id}
                            type="button"
                            onClick={() => setFeedFilter(filter.id)}
                            className={clsx(
                              "rounded-full border px-4 py-2 text-xs font-semibold transition",
                              isActive
                                ? "border-stone-950 bg-stone-950 text-white"
                                : "border-stone-200 bg-white text-stone-600 hover:border-stone-300 hover:text-stone-950"
                            )}
                          >
                            {filter.label}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>

                <div
                  id="learning-home-feed-content"
                  className={clsx(
                    "overflow-hidden transition-all duration-300 ease-out",
                    feedCollapsed ? "max-h-0" : "max-h-[240rem]"
                  )}
                >
                  {!feedCollapsed ? (
                    <div className="space-y-4">
                      {visibleFeedItems.length ? (
                        visibleFeedItems.map((item) => {
                          const buttonBusy = item.lessonId ? busyKey === `lesson:${item.lessonId}` : false;

                          return (
                            <RecentFeedCard
                              key={item.id}
                              item={item}
                              buttonBusy={buttonBusy}
                              formatRelativeTime={homeRelFmt}
                              labels={{
                                moreUpdatesAria: h.moreUpdatesAria,
                                timelineSummary: h.feedTimelineSummary,
                                timelineLatestLabel: h.feedTimelineLatestLabel,
                                timelineExpand: h.feedTimelineExpand,
                                timelineCollapse: h.feedTimelineCollapse,
                                readMore: h.feedReadMore,
                              }}
                              onOpenLesson={handleOpenLesson}
                            />
                          );
                        })
                      ) : (
                        <div className="rounded-[24px] border border-dashed border-stone-300 bg-white/70 px-5 py-8 text-sm text-stone-500">
                          {h.feedEmpty}
                        </div>
                      )}
                    </div>
                  ) : null}
                </div>
              </div>
            </section>
              </>
            )}

          </div>
        </main>
      </div>

      {lessonMenuState && lessonMenuLesson ? (
        <div
          data-lesson-menu-root
          className="fixed z-[140]"
          style={{ top: lessonMenuState.top, left: lessonMenuState.left }}
        >
          <div className="w-56 rounded-[22px] border border-stone-200 bg-white p-2 shadow-[0_18px_40px_rgba(15,23,42,0.14)]">
            <ProjectVisibilityControl
              visibility={lessonMenuLesson.visibility}
              onChange={(visibility) => void handleSetLessonVisibility(lessonMenuLesson, visibility)}
              disabled={busyKey !== null}
              label={language === "en" ? "Visibility" : "可见权限"}
              review={
                lessonMenuLesson.visibility === "public" ||
                ["blocked", "error"].includes(lessonMenuLesson.publication_review.status)
                  ? lessonMenuLesson.publication_review
                  : undefined
              }
              reviewing={
                busyKey === `visibility:lesson:${lessonMenuLesson.id}` &&
                lessonMenuLesson.visibility === "private"
              }
            />

            <div className="my-1 h-px bg-stone-100" />

            <button
              type="button"
              onClick={() => void handleShareLesson(lessonMenuLesson)}
              disabled={lessonMenuLesson.visibility !== "public" || busyKey !== null}
              className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm text-stone-700 transition hover:bg-stone-50 disabled:cursor-not-allowed disabled:text-stone-300"
              title={lessonMenuLesson.visibility === "public" ? h.sharePackageTitle : "设为 Public 后可分享"}
            >
              <Share2 className="h-4 w-4" />
              {h.share}
            </button>

            <div className="my-1 h-px bg-stone-100" />

            <button
              type="button"
              onClick={() => void handleRenameLesson(lessonMenuLesson)}
              disabled={busyKey !== null}
              className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm text-stone-700 transition hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busyKey === `rename:${lessonMenuLesson.id}` ? (
                <LoaderCircle className="h-4 w-4 animate-spin" />
              ) : (
                <PencilLine className="h-4 w-4" />
              )}
              {h.renameLesson}
            </button>

            <div className="my-1 h-px bg-stone-100" />

            <button
              type="button"
              onClick={() => void handleExportLessonPackage(lessonMenuLesson)}
              disabled={busyKey !== null}
              className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm text-stone-700 transition hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busyKey === `ridoc:export:${lessonMenuLesson.id}` ? (
                <LoaderCircle className="h-4 w-4 animate-spin" />
              ) : (
                <Download className="h-4 w-4" />
              )}
              {h.exportLessonPackage}
            </button>

            <div className="my-1 h-px bg-stone-100" />

            <button
              type="button"
              onClick={(event) => {
                const rect = event.currentTarget.getBoundingClientRect();
                setLessonMoveMenuState((current) =>
                  current?.lessonId === lessonMenuLesson.id
                    ? null
                    : {
                        lessonId: lessonMenuLesson.id,
                        top: rect.top,
                        left: rect.right + 8,
                      }
                );
              }}
              disabled={!movablePackages.length || busyKey === `move:${lessonMenuLesson.id}` || busyKey === `delete:${lessonMenuLesson.id}`}
              className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm text-stone-700 transition hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <FolderClosed className="h-4 w-4" />
              <span className="flex-1">{h.moveToPackage}</span>
              <ChevronRight className="h-4 w-4 text-stone-400" />
            </button>

            <div className="my-1 h-px bg-stone-100" />

            <button
              type="button"
              onClick={() => void handleDeleteLesson(lessonMenuLesson)}
              disabled={busyKey === `move:${lessonMenuLesson.id}` || busyKey === `delete:${lessonMenuLesson.id}`}
              className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm text-rose-600 transition hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busyKey === `delete:${lessonMenuLesson.id}` ? (
                <LoaderCircle className="h-4 w-4 animate-spin" />
              ) : (
                <Trash2 className="h-4 w-4" />
              )}
              {h.delete}
            </button>
          </div>
        </div>
      ) : null}

      <input
        ref={ridocFileInputRef}
        type="file"
        accept={RIDOC_FILE_ACCEPT}
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) {
            void handleLoadLessonPackage(file);
          }
        }}
      />

      {lessonMoveMenuState && lessonMenuLesson ? (
        <div
          data-lesson-menu-root
          className="fixed z-[145]"
          style={{ top: lessonMoveMenuState.top, left: lessonMoveMenuState.left }}
        >
          <div className="w-44 rounded-[20px] border border-stone-200 bg-white p-2 shadow-[0_18px_40px_rgba(15,23,42,0.14)]">
            {movablePackages.length ? (
              movablePackages.map((packageItem) => (
                <button
                  key={packageItem.id}
                  type="button"
                  onClick={() => void handleMoveLesson(lessonMenuLesson, packageItem.id)}
                  disabled={busyKey === `move:${lessonMenuLesson.id}` || busyKey === `delete:${lessonMenuLesson.id}`}
                  className="flex w-full items-center justify-between rounded-xl px-3 py-2 text-left text-sm text-stone-700 transition hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <span className="truncate">{packageItem.title}</span>
                  {busyKey === `move:${lessonMenuLesson.id}` ? <LoaderCircle className="h-4 w-4 animate-spin" /> : null}
                </button>
              ))
            ) : (
              <p className="px-3 py-2 text-sm text-stone-400">{h.noMovablePackages}</p>
            )}
          </div>
        </div>
      ) : null}

      <div className="fixed right-4 top-4 z-[130] w-[calc(100vw-2rem)] max-w-[42rem] xl:right-8 xl:top-6">
        {renderNotificationPanel()}
      </div>

      <nav
        aria-label={quickLinksLabel}
        className="fixed bottom-4 right-4 z-[130] flex max-w-[calc(100vw-2rem)] items-center gap-2 rounded-lg border border-stone-200 bg-white/92 p-1.5 shadow-[0_18px_40px_rgba(15,23,42,0.14)] backdrop-blur xl:bottom-6 xl:right-8"
      >
        <a
          href={GITHUB_REPOSITORY_URL}
          target="_blank"
          rel="noreferrer"
          aria-label={githubLinkLabel}
          title={githubLinkLabel}
          className="inline-flex h-10 items-center gap-2 rounded-md px-3 text-sm font-semibold text-stone-700 transition hover:bg-stone-950 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-stone-950"
        >
          <GitFork className="h-4 w-4" />
          <span>GitHub</span>
        </a>
        <Link
          href="/tech-docs"
          aria-label={projectDocsLinkLabel}
          title={projectDocsLinkLabel}
          className="inline-flex h-10 items-center gap-2 rounded-md bg-stone-950 px-3 text-sm font-semibold text-white transition hover:bg-stone-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-stone-950"
        >
          <BookText className="h-4 w-4" />
          <span>{language === "en" ? "Docs" : "项目文档"}</span>
        </Link>
      </nav>
    </div>
  );

  function renderOpenCourseSearchResults() {
    const searchText = searchQuery.trim();
    const activeFacetLabel = openCourseFacet.kind === "all" ? "全部开源课程" : openCourseFacet.value;
    const totalStars = openCourseResults.reduce((sum, course) => sum + course.stars, 0);

    return (
      <section className="mb-12">
        <div className="grid gap-5 lg:grid-cols-[15rem_minmax(0,1fr)] 2xl:grid-cols-[15rem_minmax(0,1fr)_18rem]">
          <aside className="h-fit rounded-lg border border-stone-200 bg-white/88 p-3 shadow-[0_12px_28px_rgba(15,23,42,0.04)] backdrop-blur">
            <div className="mb-3 flex items-center gap-2 px-2 text-sm font-semibold text-stone-950">
              <Code2 className="h-4 w-4" />
              <span>Filter by</span>
            </div>

            <button
              type="button"
              onClick={() => setOpenCourseFacet({ kind: "all" })}
              className={clsx(
                "flex w-full items-center justify-between rounded-md px-2 py-2 text-left text-sm transition",
                openCourseFacet.kind === "all"
                  ? "bg-stone-950 text-white"
                  : "text-stone-700 hover:bg-stone-100 hover:text-stone-950"
              )}
            >
              <span className="inline-flex items-center gap-2">
                <BookOpen className="h-4 w-4" />
                Open courses
              </span>
              <span
                className={clsx(
                  "rounded-full px-2 py-0.5 text-[10px]",
                  openCourseFacet.kind === "all" ? "bg-white/12 text-white" : "bg-stone-100 text-stone-500"
                )}
              >
                {matchingOpenCourses.length}
              </span>
            </button>

            <div className="mt-4 border-t border-stone-200 pt-4">
              <p className="px-2 text-xs font-semibold text-stone-500">课程方向</p>
              <div className="mt-2 space-y-1">
                {categoryFacetCounts.map((facet) => {
                  const isActive = openCourseFacet.kind === "category" && openCourseFacet.value === facet.value;
                  return (
                    <button
                      key={facet.value}
                      type="button"
                      onClick={() => setOpenCourseFacet({ kind: "category", value: facet.value })}
                      className={clsx(
                        "flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm transition",
                        isActive ? "bg-stone-100 text-stone-950" : "text-stone-600 hover:bg-stone-50 hover:text-stone-950"
                      )}
                    >
                      <span className="inline-flex items-center gap-2">
                        <Layers className="h-3.5 w-3.5" />
                        {facet.value}
                      </span>
                      <span className="text-xs text-stone-400">{facet.count}</span>
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="mt-4 border-t border-stone-200 pt-4">
              <p className="px-2 text-xs font-semibold text-stone-500">语言 / 学科</p>
              <div className="mt-2 space-y-1">
                {languageFacetCounts.map((facet) => {
                  const isActive = openCourseFacet.kind === "language" && openCourseFacet.value === facet.value;
                  const sampleCourse = matchingOpenCourses.find((course) => course.language === facet.value);
                  return (
                    <button
                      key={facet.value}
                      type="button"
                      onClick={() => setOpenCourseFacet({ kind: "language", value: facet.value })}
                      className={clsx(
                        "flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm transition",
                        isActive ? "bg-stone-100 text-stone-950" : "text-stone-600 hover:bg-stone-50 hover:text-stone-950"
                      )}
                    >
                      <span className="inline-flex min-w-0 items-center gap-2">
                        <span
                          className="h-2.5 w-2.5 shrink-0 rounded-full"
                          style={{ backgroundColor: sampleCourse?.languageColor ?? "#94a3b8" }}
                        />
                        <span className="truncate">{facet.value}</span>
                      </span>
                      <span className="text-xs text-stone-400">{facet.count}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          </aside>

          <div className="min-w-0">
            <div className="mb-4 flex flex-col gap-3 rounded-lg border border-stone-200 bg-white/88 px-4 py-3 shadow-[0_12px_28px_rgba(15,23,42,0.04)] backdrop-blur sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-base font-semibold text-stone-950">
                  {openCourseResults.length.toLocaleString("zh-CN")} 个开源课程结果
                </h2>
                <p className="mt-1 text-xs text-stone-500">
                  搜索 “{searchText}” · 当前筛选：{activeFacetLabel}
                </p>
              </div>

              <label className="inline-flex items-center gap-2 text-xs font-medium text-stone-500">
                Sort by
                <select
                  value={openCourseSort}
                  onChange={(event) => setOpenCourseSort(event.target.value as OpenCourseSort)}
                  className="rounded-md border border-stone-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-stone-700 outline-none transition focus:border-stone-950"
                >
                  <option value="best-match">Best match</option>
                  <option value="stars">Most stars</option>
                  <option value="updated">Recently updated</option>
                </select>
              </label>
            </div>

            <div className="space-y-3">
              {openCourseResults.length ? (
                openCourseResults.map((course) => {
                  const isCollected = collectedCourseIds.has(course.id);
                  return (
                    <article
                      key={course.id}
                      className="rounded-lg border border-stone-200 bg-white/92 p-4 shadow-[0_12px_28px_rgba(15,23,42,0.04)] transition hover:border-stone-300 hover:bg-white"
                    >
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
                            <p className="mt-1 line-clamp-2 text-sm leading-6 text-stone-700">{course.summary}</p>

                            <div className="mt-3 flex flex-wrap gap-1.5">
                              {course.topics.map((topic) => (
                                <span
                                  key={`${course.id}:${topic}`}
                                  className="rounded-full bg-sky-50 px-2.5 py-1 text-[11px] font-semibold text-sky-700"
                                >
                                  {topic}
                                </span>
                              ))}
                            </div>

                            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-stone-500">
                              <span className="inline-flex items-center gap-1.5">
                                <span
                                  className="h-2.5 w-2.5 rounded-full"
                                  style={{ backgroundColor: course.languageColor }}
                                />
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
                              <span className="inline-flex items-center gap-1">
                                <GraduationCap className="h-3.5 w-3.5" />
                                {course.lessons} lessons
                              </span>
                              <span>{course.license}</span>
                              <span>Updated {homeRelFmt(course.updatedAt)}</span>
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
                            className={clsx(
                              "inline-flex items-center justify-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-semibold transition",
                              isCollected
                                ? "border-amber-200 bg-amber-50 text-amber-700 hover:border-amber-300"
                                : "border-stone-200 bg-white text-stone-700 hover:border-stone-300 hover:text-stone-950"
                            )}
                          >
                            <Star className={clsx("h-3.5 w-3.5", isCollected && "fill-current")} />
                            {isCollected ? "已收藏" : "收藏"}
                          </button>
                        </div>
                      </div>
                    </article>
                  );
                })
              ) : (
                <div className="rounded-lg border border-dashed border-stone-300 bg-white/88 px-5 py-10 text-sm text-stone-500">
                  没有找到匹配的开源课程。换个关键词，或清除左侧筛选后再试。
                </div>
              )}
            </div>
          </div>

          <aside className="hidden h-fit space-y-3 2xl:block">
            <div className="rounded-lg border border-stone-200 bg-white/88 p-4 shadow-[0_12px_28px_rgba(15,23,42,0.04)]">
              <div className="flex items-center gap-2 text-sm font-semibold text-stone-950">
                <Bookmark className="h-4 w-4 text-amber-500" />
                收藏的开源课程
              </div>
              <p className="mt-2 text-sm leading-6 text-stone-600">
                已收藏 {collectedOpenCourseCount} 个项目，可在个人主页继续查看和管理。
              </p>
              <Link
                href="/profile"
                className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-md border border-stone-200 bg-stone-50 px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:bg-white hover:text-stone-950"
              >
                打开个人主页
                <ArrowUpRight className="h-4 w-4" />
              </Link>
            </div>

            <div className="rounded-lg border border-stone-200 bg-white/88 p-4 shadow-[0_12px_28px_rgba(15,23,42,0.04)]">
              <div className="flex items-center gap-2 text-sm font-semibold text-stone-950">
                <Eye className="h-4 w-4 text-sky-600" />
                搜索概览
              </div>
              <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
                <div>
                  <dt className="text-xs text-stone-400">Stars</dt>
                  <dd className="mt-1 font-semibold text-stone-900">{formatCompactNumber(totalStars)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-stone-400">Topics</dt>
                  <dd className="mt-1 font-semibold text-stone-900">
                    {new Set(openCourseResults.flatMap((course) => course.topics)).size}
                  </dd>
                </div>
              </dl>
            </div>
          </aside>
        </div>
      </section>
    );
  }

  function renderSelectedPackagePanel() {
    if (!selectedCoursePackage) {
      return null;
    }
    const isDeletingPackage = busyKey === `package:delete:${selectedCoursePackage.id}`;
    const isRenamingPackage = busyKey === `package:rename:${selectedCoursePackage.id}`;
    const isUpdatingVisibility = busyKey === `visibility:package:${selectedCoursePackage.id}`;
    const isReviewingPublication = isUpdatingVisibility && selectedCoursePackage.visibility === "private";
    const packageActionBusy = isDeletingPackage || isRenamingPackage || isUpdatingVisibility;

    return (
      <div className="w-full rounded-[28px] border border-white/80 bg-white/95 p-5 shadow-[0_18px_50px_rgba(15,23,42,0.1)] backdrop-blur">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-stone-400">
              {h.currentPackage}
            </p>
            <h4 className="mt-2 truncate text-lg font-semibold text-stone-950">{selectedCoursePackage.title}</h4>
            <div className="mt-2 flex h-3.5 origin-left scale-[0.82] flex-nowrap items-center gap-0.5">
              <ProjectVisibilityControl
                compact
                visibility={selectedCoursePackage.visibility}
                onChange={(visibility) => void handleSetSelectedPackageVisibility(visibility)}
                disabled={packageActionBusy}
                ariaLabelPrefix="课程包设为"
                reviewing={isReviewingPublication}
              />
              <button
                type="button"
                onClick={() => void handleDeleteSelectedPackage()}
                disabled={packageActionBusy}
                className="inline-flex h-3.5 shrink-0 items-center gap-px rounded-full border border-rose-100 bg-rose-50 px-1 text-[8px] font-normal leading-none text-rose-600 transition hover:border-rose-200 hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-45"
                title={h.deletePackageTitle}
              >
                {isDeletingPackage ? <LoaderCircle className="h-2 w-2 animate-spin" /> : <Trash2 className="h-2 w-2" />}
                {h.delete}
              </button>
              <button
                type="button"
                onClick={() => void handleShareSelectedPackage()}
                disabled={packageActionBusy || selectedCoursePackage.visibility !== "public"}
                className="inline-flex h-3.5 shrink-0 items-center gap-px rounded-full border border-stone-200 bg-white px-1 text-[8px] font-normal leading-none text-stone-600 transition hover:border-stone-300 hover:text-stone-950 disabled:cursor-not-allowed disabled:opacity-45"
                title={selectedCoursePackage.visibility === "public" ? h.sharePackageTitle : "设为 Public 后可分享"}
              >
                <Share2 className="h-2 w-2" />
                {h.share}
              </button>
              <button
                type="button"
                onClick={() => void handleRenameSelectedPackage()}
                disabled={packageActionBusy}
                className="inline-flex h-3.5 shrink-0 items-center gap-px rounded-full border border-stone-200 bg-white px-1 text-[8px] font-normal leading-none text-stone-600 transition hover:border-stone-300 hover:text-stone-950 disabled:cursor-not-allowed disabled:opacity-45"
                title={h.renamePackageTitle}
              >
                {isRenamingPackage ? <LoaderCircle className="h-2 w-2 animate-spin" /> : <PencilLine className="h-2 w-2" />}
                {h.renamePackage}
              </button>
            </div>
            <PublicationReviewNotice
              reviewing={isReviewingPublication}
              review={
                selectedCoursePackage.visibility === "public" ||
                ["blocked", "error"].includes(selectedCoursePackage.publication_review.status)
                  ? selectedCoursePackage.publication_review
                  : undefined
              }
            />
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <span className="rounded-full bg-stone-100 px-2.5 py-1 text-[10px] font-semibold text-stone-600">
              {h.packageLessonCount(selectedPackageLessons.length)}
            </span>
            <button
              type="button"
              onClick={() => {
                setSelectedPackageId(null);
                setSelectedLessonId(null);
                setPackageLessonsExpanded(false);
              }}
              className="flex h-8 w-8 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-600 transition hover:border-stone-300 hover:text-stone-950"
              aria-label={h.collapsePackageLessons}
              title={h.collapsePackageLessons}
            >
              <ChevronDown className="h-4 w-4 rotate-180" />
            </button>
          </div>
        </div>

        <div className="mt-4">
          <div className="custom-scrollbar max-h-[28rem] space-y-2 overflow-y-auto pr-1">
            {selectedPackageLessons.length ? (
              selectedPackageLessons.map((lesson) => {
                const isPreviewActive = lesson.id === selectedPackageActiveLesson?.id;
                const buttonBusy = busyKey === `lesson:${lesson.id}`;
                return (
                  <button
                    key={lesson.id}
                    type="button"
                    data-lesson-selection-root
                    onClick={() => void handleOpenLesson(lesson.id)}
                    disabled={buttonBusy}
                    className={clsx(
                      "w-full rounded-2xl border px-3 py-3 text-left transition disabled:cursor-wait",
                      isPreviewActive
                        ? "border-stone-950 bg-stone-950 text-white"
                        : "border-stone-200 bg-stone-50/90 text-stone-800 hover:border-stone-300 hover:bg-white"
                    )}
                  >
                    <div className="flex items-start gap-3">
                      <div
                        className={clsx(
                          "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border",
                          isPreviewActive
                            ? "border-white/10 bg-white/10 text-white"
                            : "border-stone-200 bg-white text-stone-500"
                        )}
                      >
                        {buttonBusy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <BookText className="h-4 w-4" />}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <p className="truncate text-sm font-semibold">{lesson.title}</p>
                          <span className={clsx("shrink-0 text-[10px]", isPreviewActive ? "text-white/70" : "text-stone-400")}>
                            {homeRelFmt(lesson.updated_at)}
                          </span>
                        </div>
                        <p className={clsx("mt-1 line-clamp-2 text-xs leading-5", isPreviewActive ? "text-white/75" : "text-stone-500")}>
                          {lesson.summary || h.lessonSummaryFallback}
                        </p>
                      </div>
                    </div>
                  </button>
                );
              })
            ) : (
              <div className="rounded-2xl border border-dashed border-stone-300 bg-stone-50/80 px-4 py-5 text-sm text-stone-500">
                {h.selectedPackageEmpty}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  function renderNotificationPanel() {
    return (
      <div className="flex flex-col items-end gap-4">
        <div className="flex flex-nowrap items-center justify-end gap-3 whitespace-nowrap">
          <Link
            href="/wallet"
            className="group relative hidden h-11 items-center gap-2 rounded-full border border-emerald-100 bg-white px-3 text-sm font-semibold text-stone-700 shadow-[0_10px_24px_rgba(16,185,129,0.10)] transition hover:-translate-y-0.5 hover:bg-emerald-600 hover:text-white sm:flex"
            aria-label="打开积分与充值"
          >
            <span className="flex h-8 w-8 items-center justify-center rounded-full bg-emerald-50 text-emerald-600 transition group-hover:bg-white">
              <WalletCards className="h-4 w-4" />
            </span>
            <span>充值</span>
          </Link>
          <Link
            href="/community"
            className="group relative hidden h-11 items-center gap-2 rounded-full border border-sky-100 bg-white px-3 text-sm font-semibold text-stone-700 shadow-[0_10px_24px_rgba(14,165,233,0.10)] transition hover:-translate-y-0.5 hover:bg-sky-500 hover:text-white hover:shadow-[0_14px_28px_rgba(14,165,233,0.18)] sm:flex"
            aria-label="打开学习社区"
          >
            <span className="relative flex h-8 w-8 items-center justify-center rounded-full bg-sky-50 text-sky-500 transition group-hover:bg-white group-hover:text-sky-500">
              <MessageCircle className="h-4 w-4" />
            </span>
            <span>社区</span>
          </Link>
          <Link
            href="/trending"
            className="group relative hidden h-11 items-center gap-2 rounded-full border border-orange-100 bg-white px-3 text-sm font-semibold text-stone-700 shadow-[0_10px_24px_rgba(249,115,22,0.10)] transition hover:-translate-y-0.5 hover:bg-orange-500 hover:text-white hover:shadow-[0_14px_28px_rgba(249,115,22,0.18)] sm:flex"
            aria-label="打开热门项目"
          >
            <span className="relative flex h-8 w-8 items-center justify-center rounded-full bg-orange-50 text-orange-500 transition group-hover:bg-white group-hover:text-orange-500">
              <Flame className="h-4 w-4" />
            </span>
            <span>热门</span>
          </Link>
          <Link
            href="/profile?tab=stars"
            className="group relative hidden h-11 items-center gap-2 rounded-full border border-amber-100 bg-white px-3 text-sm font-semibold text-stone-700 shadow-[0_10px_24px_rgba(245,158,11,0.10)] transition hover:-translate-y-0.5 hover:bg-amber-500 hover:text-white hover:shadow-[0_14px_28px_rgba(245,158,11,0.18)] sm:flex"
            aria-label="打开 Stars 收藏"
          >
            <span className="relative flex h-8 w-8 items-center justify-center rounded-full bg-amber-50 text-amber-500 transition group-hover:bg-white group-hover:text-amber-500">
              <Star className="h-4 w-4" />
              {collectedOpenCourseCount ? (
                <span className="absolute -right-1.5 -top-1.5 min-w-5 rounded-full bg-amber-500 px-1.5 py-0.5 text-[10px] font-semibold leading-none text-white ring-2 ring-white">
                  {collectedOpenCourseCount > 99 ? "99+" : collectedOpenCourseCount}
                </span>
              ) : null}
            </span>
            <span>Star</span>
          </Link>
          <Link
            href="/following"
            className="group relative hidden h-11 items-center gap-2 rounded-full border border-rose-100 bg-white px-3 text-sm font-semibold text-stone-700 shadow-[0_10px_24px_rgba(244,63,94,0.10)] transition hover:-translate-y-0.5 hover:bg-rose-500 hover:text-white hover:shadow-[0_14px_28px_rgba(244,63,94,0.18)] sm:flex"
            aria-label="打开关注动态"
          >
            <span className="relative flex h-8 w-8 items-center justify-center rounded-full bg-rose-50 text-rose-500 transition group-hover:bg-white group-hover:text-rose-500">
              <Activity className="h-4 w-4" />
              {followingUnreadCount ? (
                <span className="absolute -right-1.5 -top-1.5 min-w-5 rounded-full bg-rose-500 px-1.5 py-0.5 text-[10px] font-semibold leading-none text-white ring-2 ring-white">
                  {followingBadge}
                </span>
              ) : null}
            </span>
            <span>动态</span>
          </Link>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setNotificationOpen((current) => !current)}
              className="relative flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-700 shadow-[0_10px_24px_rgba(15,23,42,0.08)] transition hover:shadow-[0_14px_28px_rgba(15,23,42,0.12)]"
              aria-label={h.notificationToggleAria}
              title={h.notificationToggleAria}
            >
              <Bell className="h-5 w-5" />
              {followingUnreadCount ? (
                <span className="absolute -right-1 -top-1 min-w-5 rounded-full bg-rose-500 px-1.5 py-0.5 text-[10px] font-semibold leading-none text-white ring-2 ring-white">
                  {followingBadge}
                </span>
              ) : null}
            </button>
            <button
              type="button"
              onClick={handleToggleInterfaceLanguage}
              className="group flex h-11 shrink-0 items-center gap-1.5 rounded-full border border-stone-200 bg-white px-3 text-[11px] font-semibold text-stone-600 shadow-[0_8px_18px_rgba(15,23,42,0.07)] transition hover:-translate-y-0.5 hover:border-sky-200 hover:bg-sky-50 hover:text-sky-700"
              aria-label={language === "en" ? h.languageSwitchToChinese : h.languageSwitchToEnglish}
              title={language === "en" ? h.languageSwitchToChinese : h.languageSwitchToEnglish}
            >
              <Languages className="h-3.5 w-3.5" />
              <span>{language === "en" ? "中" : "EN"}</span>
            </button>
          </div>
          <AccountMenu
            ariaLabel={txt.profileHome.avatarAlt}
            fallbackAvatarUrl="https://api.dicebear.com/9.x/glass/svg?seed=kai-fang-ke-tang"
            trigger="avatar"
          />
        </div>

        {notificationOpen ? (
          <div className="w-full max-w-[27rem] rounded-[28px] border border-white/80 bg-white/92 p-5 shadow-[0_18px_50px_rgba(15,23,42,0.08)] backdrop-blur">
            <div className="mb-4 flex items-center justify-between">
              <h4 className="text-[11px] font-semibold uppercase tracking-[0.28em] text-stone-400">消息推送</h4>
              <span className="rounded-full bg-rose-500 px-2 py-1 text-[10px] font-semibold text-white">
                {followingUnreadCount ? `${followingBadge} NEW` : "已同步"}
              </span>
            </div>

            <div className="space-y-3">
              {notificationUpdates.length ? (
                notificationUpdates.map((item) => (
                  <Link
                    key={item.update.id}
                    href="/following"
                    className="group block rounded-lg border border-stone-300 bg-white p-3 shadow-sm transition hover:border-stone-400"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex min-w-0 gap-2.5">
                        <Image
                          src={creatorAvatarUrl(item.creator)}
                          alt=""
                          className="mt-0.5 h-8 w-8 shrink-0 rounded-full border border-stone-200 bg-stone-100"
                          width={32}
                          height={32}
                          unoptimized
                        />
                        <div className="min-w-0">
                          <p className="line-clamp-2 text-xs leading-5 text-stone-600">
                            <span className="font-semibold text-stone-950">{item.creator.name}</span>{" "}
                            {followedUpdateActionLabel(item.update.updateKind)}{" "}
                            <span className="font-semibold text-stone-950">{item.update.courseTitle}</span>
                          </p>
                          <p className="mt-0.5 text-[11px] text-stone-400">{homeRelFmt(item.update.updatedAt)}</p>
                        </div>
                      </div>

                      <MoreHorizontal className="mt-1 h-4 w-4 shrink-0 text-stone-400" />
                    </div>

                    <p className="mt-3 line-clamp-2 text-sm font-semibold leading-5 text-stone-950 group-hover:text-stone-800">
                      {item.update.moduleTitle}
                    </p>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <span
                        className={clsx(
                          "rounded-full px-2 py-0.5 text-[10px] font-semibold",
                          followedUpdateTone(item.update.updateKind)
                        )}
                      >
                        {FOLLOWED_UPDATE_KIND_LABELS[item.update.updateKind]}
                      </span>
                      <span className="text-[11px] text-stone-400">{item.update.lessonCount} 课</span>
                    </div>

                    <div className="mt-3 rounded-md bg-[#f6f8fa] p-3">
                      <div className="border-b border-stone-200 pb-2">
                        <p className="text-sm font-semibold text-stone-950">
                          {followedUpdatePreviewHeading(item.update.updateKind)}
                        </p>
                      </div>
                      <p className="mt-2 line-clamp-3 text-xs leading-5 text-stone-600">{item.update.summary}</p>
                      <span className="mt-3 inline-flex text-[11px] font-semibold text-stone-800 underline underline-offset-2">
                        Read more
                      </span>
                    </div>

                    <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-stone-500">
                      {item.update.tags.slice(0, 3).map((tag) => (
                        <span key={`${item.update.id}:notification:${tag}`} className="rounded-md border border-stone-200 px-2 py-0.5">
                          {tag}
                        </span>
                      ))}
                    </div>
                  </Link>
                ))
              ) : (
                <div className="rounded-2xl border border-dashed border-stone-200 bg-stone-50 px-4 py-5 text-sm text-stone-500">
                  关注他人项目后，这里只显示这些项目的更新推送。
                </div>
              )}
            </div>

            <Link
              href="/following"
              className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-full border border-stone-200 bg-stone-50 px-4 py-3 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:bg-white hover:text-stone-950"
            >
              查看全部动态
              <ArrowUpRight className="h-4 w-4" />
            </Link>
          </div>
        ) : null}

      </div>
    );
  }
}
