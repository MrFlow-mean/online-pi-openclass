"use client";

import clsx from "clsx";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  BookOpen,
  CircleHelp,
  Clock3,
  Flame,
  Hash,
  MessageCircle,
  Plus,
  Search,
  Send,
  Sparkles,
  UsersRound,
  X,
} from "lucide-react";

import { AccountMenu } from "@/components/account-menu";
import { BrandMark } from "@/components/brand-mark";
import { api } from "@/lib/api";
import { communityApi } from "@/lib/community-api";
import type {
  CommunityFeedSort,
  CommunityPost,
  CommunityPostDetail,
  CommunityPostType,
  CommunitySpace,
  UserView,
} from "@/types";


const postTypeLabels: Record<CommunityPostType, string> = {
  question: "问题",
  discussion: "讨论",
  resource: "资料",
  study_note: "学习记录",
};

const postTypeStyles: Record<CommunityPostType, string> = {
  question: "border-amber-200 bg-amber-50 text-amber-800",
  discussion: "border-sky-200 bg-sky-50 text-sky-800",
  resource: "border-emerald-200 bg-emerald-50 text-emerald-800",
  study_note: "border-violet-200 bg-violet-50 text-violet-800",
};

const feedSorts: Array<{ id: CommunityFeedSort; label: string; icon: typeof Clock3 }> = [
  { id: "recent", label: "最新", icon: Clock3 },
  { id: "hot", label: "热门", icon: Flame },
  { id: "unanswered", label: "待回答", icon: CircleHelp },
];


function formatRelativeTime(value: string) {
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) {
    return "刚刚";
  }
  const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60_000));
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} 天前`;
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" }).format(new Date(value));
}


function postExcerpt(body: string) {
  const normalized = body.replace(/\s+/g, " ").trim();
  return normalized.length > 190 ? `${normalized.slice(0, 190)}…` : normalized;
}


function PostCard({ post, onOpen, onTag }: {
  post: CommunityPost;
  onOpen: () => void;
  onTag: (tag: string) => void;
}) {
  return (
    <article className="group rounded-2xl border border-stone-200 bg-white shadow-[0_8px_28px_rgba(28,25,23,0.04)] transition hover:-translate-y-0.5 hover:border-stone-300 hover:shadow-[0_14px_36px_rgba(28,25,23,0.08)]">
      <div className="p-5 sm:p-6">
        <div className="flex items-start gap-4">
          <div className="hidden w-12 shrink-0 text-center sm:block">
            <ArrowUp className="mx-auto h-4 w-4 text-stone-400" />
            <p className="my-1 text-sm font-bold text-stone-800">{post.vote_score}</p>
            <ArrowDown className="mx-auto h-4 w-4 text-stone-300" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2 text-xs text-stone-500">
              <span className={clsx("rounded-full border px-2 py-0.5 font-semibold", postTypeStyles[post.post_type])}>
                {postTypeLabels[post.post_type]}
              </span>
              <span className="font-semibold text-stone-700">c/{post.community_name}</span>
              <span>·</span>
              <span>{post.author_display_name}</span>
              <span>·</span>
              <span>{formatRelativeTime(post.created_at)}</span>
            </div>
            <h2 className="mt-3 text-lg font-semibold leading-snug text-stone-950 transition group-hover:text-[#d74f7b]">
              <button type="button" onClick={onOpen} className="text-left">{post.title}</button>
            </h2>
            <button type="button" onClick={onOpen} className="mt-2 block w-full text-left text-sm leading-6 text-stone-600">{postExcerpt(post.body)}</button>
            <div className="mt-4 flex flex-wrap items-center gap-2">
              {post.tags.map((tag) => (
                <button type="button" onClick={() => onTag(tag)} key={tag} className="rounded-full bg-stone-100 px-2.5 py-1 text-xs font-medium text-stone-600 transition hover:bg-stone-200 hover:text-stone-950">
                  #{tag}
                </button>
              ))}
              <span className="ml-auto inline-flex items-center gap-1.5 text-xs font-semibold text-stone-500">
                <MessageCircle className="h-3.5 w-3.5" />
                {post.comment_count} 条讨论
              </span>
            </div>
          </div>
        </div>
      </div>
    </article>
  );
}


function ComposerPanel({
  kind,
  spaces,
  initialCommunitySlug,
  onClose,
  onCreated,
}: {
  kind: "space" | "post";
  spaces: CommunitySpace[];
  initialCommunitySlug: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [communitySlug, setCommunitySlug] = useState(initialCommunitySlug || spaces[0]?.slug || "");
  const [postType, setPostType] = useState<CommunityPostType>("discussion");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [tags, setTags] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      if (kind === "space") {
        await communityApi.createSpace({ name, description });
      } else {
        await communityApi.createPost({
          community_slug: communitySlug,
          post_type: postType,
          title,
          body,
          tags: tags.split(/[，,]/).map((tag) => tag.trim().replace(/^#/, "")).filter(Boolean),
        });
      }
      onCreated();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "发布失败，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-[140] flex items-end justify-center bg-stone-950/35 p-0 backdrop-blur-sm sm:items-center sm:p-6" role="presentation">
      <section role="dialog" aria-modal="true" aria-labelledby="community-composer-title" className="max-h-[92vh] w-full max-w-2xl overflow-y-auto rounded-t-[28px] border border-white/60 bg-[#fffdf8] p-6 shadow-2xl sm:rounded-[28px] sm:p-8">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#d74f7b]">OpenClass Community</p>
            <h2 id="community-composer-title" className="mt-2 text-2xl font-semibold text-stone-950">
              {kind === "space" ? "创建主题社区" : "发布学习内容"}
            </h2>
            <p className="mt-2 text-sm leading-6 text-stone-500">
              {kind === "space" ? "用一个清晰主题聚集长期交流。" : "问题、讨论、资料和学习过程都可以成为帖子。"}
            </p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭发布窗口" className="rounded-full border border-stone-200 bg-white p-2 text-stone-500 transition hover:text-stone-950">
            <X className="h-5 w-5" />
          </button>
        </div>

        <form className="mt-7 space-y-5" onSubmit={handleSubmit}>
          {kind === "space" ? (
            <>
              <label className="block text-sm font-semibold text-stone-800">
                社区名称
                <input required minLength={2} maxLength={80} value={name} onChange={(event) => setName(event.target.value)} placeholder="输入一个可持续讨论的学习主题" className="mt-2 w-full rounded-xl border border-stone-200 bg-white px-4 py-3 font-normal outline-none transition focus:border-stone-500" />
              </label>
              <label className="block text-sm font-semibold text-stone-800">
                社区说明
                <textarea maxLength={500} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="这里适合讨论什么，以及成员可以怎样参与" rows={4} className="mt-2 w-full resize-none rounded-xl border border-stone-200 bg-white px-4 py-3 font-normal leading-6 outline-none transition focus:border-stone-500" />
              </label>
            </>
          ) : (
            <>
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="block text-sm font-semibold text-stone-800">
                  发布到
                  <select required value={communitySlug} onChange={(event) => setCommunitySlug(event.target.value)} className="mt-2 w-full rounded-xl border border-stone-200 bg-white px-4 py-3 font-normal outline-none focus:border-stone-500">
                    {spaces.map((space) => <option key={space.id} value={space.slug}>{space.name}</option>)}
                  </select>
                </label>
                <label className="block text-sm font-semibold text-stone-800">
                  内容类型
                  <select value={postType} onChange={(event) => setPostType(event.target.value as CommunityPostType)} className="mt-2 w-full rounded-xl border border-stone-200 bg-white px-4 py-3 font-normal outline-none focus:border-stone-500">
                    {Object.entries(postTypeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </label>
              </div>
              <label className="block text-sm font-semibold text-stone-800">
                标题
                <input required minLength={4} maxLength={180} value={title} onChange={(event) => setTitle(event.target.value)} placeholder="让读者一眼理解你想讨论的内容" className="mt-2 w-full rounded-xl border border-stone-200 bg-white px-4 py-3 font-normal outline-none focus:border-stone-500" />
              </label>
              <label className="block text-sm font-semibold text-stone-800">
                正文
                <textarea required maxLength={40000} value={body} onChange={(event) => setBody(event.target.value)} placeholder="写下背景、自己的理解、证据或希望得到的帮助" rows={8} className="mt-2 w-full resize-y rounded-xl border border-stone-200 bg-white px-4 py-3 font-normal leading-7 outline-none focus:border-stone-500" />
              </label>
              <label className="block text-sm font-semibold text-stone-800">
                标签
                <input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="用逗号分隔，最多 8 个" className="mt-2 w-full rounded-xl border border-stone-200 bg-white px-4 py-3 font-normal outline-none focus:border-stone-500" />
              </label>
            </>
          )}
          {error ? <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</p> : null}
          <div className="flex justify-end gap-3 border-t border-stone-200 pt-5">
            <button type="button" onClick={onClose} className="rounded-xl border border-stone-200 bg-white px-4 py-2.5 text-sm font-semibold text-stone-700">取消</button>
            <button type="submit" disabled={submitting || (kind === "post" && !communitySlug)} className="inline-flex items-center gap-2 rounded-xl bg-stone-950 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-stone-800 disabled:cursor-not-allowed disabled:opacity-50">
              <Send className="h-4 w-4" />
              {submitting ? "正在发布…" : kind === "space" ? "创建社区" : "发布帖子"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}


function PostDetailView({
  detail,
  onBack,
  onVote,
  onComment,
}: {
  detail: CommunityPostDetail;
  onBack: () => void;
  onVote: (value: -1 | 0 | 1) => void;
  onComment: (body: string, parentCommentId?: string | null) => Promise<void>;
}) {
  const [commentBody, setCommentBody] = useState("");
  const [replyTo, setReplyTo] = useState<{ id: string; name: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const post = detail.post;

  async function handleComment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!commentBody.trim()) return;
    setSubmitting(true);
    setError("");
    try {
      await onComment(commentBody.trim(), replyTo?.id);
      setCommentBody("");
      setReplyTo(null);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "评论发布失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section aria-label="帖子详情" className="rounded-2xl border border-stone-200 bg-white p-5 shadow-[0_12px_34px_rgba(28,25,23,0.06)] sm:p-7">
      <button type="button" onClick={onBack} className="inline-flex items-center gap-2 text-sm font-semibold text-stone-500 transition hover:text-stone-950">
        <ArrowLeft className="h-4 w-4" /> 返回帖子列表
      </button>
      <div className="mt-6 flex gap-4">
        <div className="flex w-10 shrink-0 flex-col items-center gap-1 rounded-xl bg-stone-50 py-2">
          <button type="button" aria-label="赞同帖子" onClick={() => onVote(post.viewer_vote === 1 ? 0 : 1)} className={clsx("rounded-lg p-1.5 transition", post.viewer_vote === 1 ? "bg-[#ffe7ef] text-[#d74f7b]" : "text-stone-400 hover:bg-stone-200 hover:text-stone-800")}><ArrowUp className="h-5 w-5" /></button>
          <span className="text-sm font-bold text-stone-900">{post.vote_score}</span>
          <button type="button" aria-label="不赞同帖子" onClick={() => onVote(post.viewer_vote === -1 ? 0 : -1)} className={clsx("rounded-lg p-1.5 transition", post.viewer_vote === -1 ? "bg-sky-100 text-sky-700" : "text-stone-400 hover:bg-stone-200 hover:text-stone-800")}><ArrowDown className="h-5 w-5" /></button>
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-xs text-stone-500">
            <span className={clsx("rounded-full border px-2 py-0.5 font-semibold", postTypeStyles[post.post_type])}>{postTypeLabels[post.post_type]}</span>
            <span className="font-semibold text-stone-700">c/{post.community_name}</span>
            <span>· {post.author_display_name}</span>
            <span>· {formatRelativeTime(post.created_at)}</span>
          </div>
          <h1 className="mt-3 text-2xl font-semibold leading-tight text-stone-950 sm:text-3xl">{post.title}</h1>
          <p className="mt-5 whitespace-pre-wrap text-[15px] leading-7 text-stone-700">{post.body}</p>
          <div className="mt-5 flex flex-wrap gap-2">
            {post.tags.map((tag) => <span key={tag} className="rounded-full bg-stone-100 px-2.5 py-1 text-xs font-medium text-stone-600">#{tag}</span>)}
          </div>
        </div>
      </div>

      <div className="mt-8 border-t border-stone-200 pt-6">
        <h2 className="flex items-center gap-2 text-lg font-semibold text-stone-950"><MessageCircle className="h-5 w-5" />{detail.comments.length} 条讨论</h2>
        <form onSubmit={handleComment} className="mt-4 rounded-2xl border border-stone-200 bg-[#faf9f6] p-4">
          {replyTo ? <div className="mb-3 flex items-center justify-between rounded-lg bg-white px-3 py-2 text-xs text-stone-600"><span>回复 {replyTo.name}</span><button type="button" onClick={() => setReplyTo(null)} aria-label="取消回复"><X className="h-3.5 w-3.5" /></button></div> : null}
          <textarea aria-label="写评论" value={commentBody} onChange={(event) => setCommentBody(event.target.value)} placeholder="补充理解、提出追问或给出可验证的回答" rows={3} className="w-full resize-y bg-transparent text-sm leading-6 outline-none placeholder:text-stone-400" />
          {error ? <p role="alert" className="mt-2 text-sm text-rose-700">{error}</p> : null}
          <div className="mt-3 flex justify-end"><button type="submit" disabled={submitting || !commentBody.trim()} className="inline-flex items-center gap-2 rounded-lg bg-stone-950 px-4 py-2 text-xs font-semibold text-white disabled:opacity-40"><Send className="h-3.5 w-3.5" />{submitting ? "发送中…" : "参与讨论"}</button></div>
        </form>
        <div className="mt-5 space-y-3">
          {detail.comments.map((comment) => (
            <article key={comment.id} className={clsx("rounded-xl border border-stone-200 bg-white p-4", comment.parent_comment_id && "ml-6 border-l-4 border-l-stone-300")}>
              <div className="flex flex-wrap items-center gap-2 text-xs text-stone-500"><span className="font-semibold text-stone-800">{comment.author_display_name}</span><span>·</span><span>{formatRelativeTime(comment.created_at)}</span></div>
              <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-stone-700">{comment.body}</p>
              <button type="button" onClick={() => setReplyTo({ id: comment.id, name: comment.author_display_name })} className="mt-3 text-xs font-semibold text-stone-500 hover:text-stone-950">回复</button>
            </article>
          ))}
          {!detail.comments.length ? <p className="rounded-xl border border-dashed border-stone-300 px-4 py-8 text-center text-sm text-stone-500">还没有讨论，成为第一个回应的人。</p> : null}
        </div>
      </div>
    </section>
  );
}


export function CommunityHome() {
  const router = useRouter();
  const [user, setUser] = useState<UserView | null>(null);
  const [spaces, setSpaces] = useState<CommunitySpace[]>([]);
  const [posts, setPosts] = useState<CommunityPost[]>([]);
  const [selectedSpaceSlug, setSelectedSpaceSlug] = useState("");
  const [selectedPost, setSelectedPost] = useState<CommunityPostDetail | null>(null);
  const [followedSpaceIds, setFollowedSpaceIds] = useState<Set<string>>(new Set());
  const [feedSort, setFeedSort] = useState<CommunityFeedSort>("recent");
  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const [composer, setComposer] = useState<"space" | "post" | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadSpaces = useCallback(async () => {
    const nextSpaces = await communityApi.listSpaces("active");
    setSpaces(nextSpaces);
  }, []);

  const loadPosts = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const nextPosts = await communityApi.listPosts({ community: selectedSpaceSlug, tag: tagFilter, q: query, sort: feedSort });
      setPosts(nextPosts);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "社区内容加载失败");
    } finally {
      setLoading(false);
    }
  }, [feedSort, query, selectedSpaceSlug, tagFilter]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void loadSpaces().catch((requestError) => setError(requestError instanceof Error ? requestError.message : "社区列表加载失败"));
      void api.getCurrentUser().then(setUser).catch(() => setUser(null));
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [loadSpaces]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => void loadPosts(), 0);
    return () => window.clearTimeout(timeoutId);
  }, [loadPosts]);

  const activeSpace = useMemo(() => spaces.find((space) => space.slug === selectedSpaceSlug) ?? null, [selectedSpaceSlug, spaces]);
  const popularTags = useMemo(() => {
    const counts = new Map<string, number>();
    posts.forEach((post) => post.tags.forEach((tag) => counts.set(tag, (counts.get(tag) ?? 0) + 1)));
    return Array.from(counts, ([tag, count]) => ({ tag, count })).sort((left, right) => right.count - left.count || left.tag.localeCompare(right.tag, "zh-CN")).slice(0, 8);
  }, [posts]);

  function canWrite() {
    if (user?.role === "user" || user?.role === "admin") return true;
    router.push("/login?next=%2Fcommunity");
    return false;
  }

  function openComposer(kind: "space" | "post") {
    if (!canWrite()) return;
    if (kind === "post" && !spaces.length) {
      setComposer("space");
      return;
    }
    setComposer(kind);
  }

  async function handleOpenPost(postId: string) {
    setError("");
    try {
      setSelectedPost(await communityApi.getPost(postId));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "帖子加载失败");
    }
  }

  async function handleVote(value: -1 | 0 | 1) {
    if (!selectedPost || !canWrite()) return;
    try {
      const result = await communityApi.vote(selectedPost.post.id, value);
      const updatePost = (post: CommunityPost) => post.id === result.post_id ? { ...post, viewer_vote: result.viewer_vote, vote_score: result.vote_score } : post;
      setSelectedPost((current) => current ? { ...current, post: updatePost(current.post) } : current);
      setPosts((current) => current.map(updatePost));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "投票失败");
    }
  }

  async function handleComment(body: string, parentCommentId?: string | null) {
    if (!selectedPost || !canWrite()) return;
    const comment = await communityApi.addComment(selectedPost.post.id, body, parentCommentId);
    setSelectedPost((current) => current ? { ...current, post: { ...current.post, comment_count: current.post.comment_count + 1 }, comments: [...current.comments, comment] } : current);
    setPosts((current) => current.map((post) => post.id === comment.post_id ? { ...post, comment_count: post.comment_count + 1 } : post));
  }

  async function handleFollow(space: CommunitySpace) {
    if (!canWrite()) return;
    try {
      const result = await communityApi.followSpace(space.slug);
      setSpaces((current) => current.map((item) => item.id === result.community_id ? { ...item, follower_count: result.follower_count } : item));
      setFollowedSpaceIds((current) => new Set(current).add(result.community_id));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "关注失败");
    }
  }

  async function handleCreated() {
    setComposer(null);
    await Promise.all([loadSpaces(), loadPosts()]);
  }

  return (
    <main className="min-h-screen bg-[#f5f3ee] text-stone-900">
      <header className="sticky top-0 z-40 border-b border-stone-200/80 bg-[#f5f3ee]/92 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-[1480px] items-center gap-4 px-4 sm:px-6">
          <Link href="/home" aria-label="返回学习主页" className="flex items-center gap-2"><BrandMark className="h-8 w-8" /><span className="hidden text-sm font-bold tracking-tight sm:inline">OpenClass</span></Link>
          <span className="hidden h-5 w-px bg-stone-300 sm:block" />
          <span className="hidden text-sm font-semibold text-stone-600 sm:inline">学习社区</span>
          <form onSubmit={(event) => { event.preventDefault(); setQuery(searchInput.trim()); setSelectedPost(null); }} className="relative mx-auto w-full max-w-xl">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" />
            <input value={searchInput} onChange={(event) => setSearchInput(event.target.value)} placeholder="搜索问题、讨论和学习记录" className="h-10 w-full rounded-xl border border-stone-200 bg-white pl-10 pr-4 text-sm outline-none transition focus:border-stone-400 focus:ring-4 focus:ring-stone-200/60" />
          </form>
          <button type="button" onClick={() => openComposer("post")} className="hidden items-center gap-2 rounded-xl bg-stone-950 px-4 py-2.5 text-sm font-semibold text-white sm:inline-flex"><Plus className="h-4 w-4" />发布</button>
          <AccountMenu compact />
        </div>
      </header>

      <div className="mx-auto grid max-w-[1480px] gap-5 px-4 py-6 sm:px-6 lg:grid-cols-[240px_minmax(0,1fr)] xl:grid-cols-[240px_minmax(0,1fr)_290px]">
        <aside className="min-w-0">
          <div className="sticky top-22 rounded-2xl border border-stone-200 bg-white p-3 shadow-[0_8px_28px_rgba(28,25,23,0.04)]">
            <div className="flex items-center justify-between px-2 pb-3 pt-1"><h2 className="flex items-center gap-2 text-sm font-semibold"><UsersRound className="h-4 w-4" />主题社区</h2><button type="button" onClick={() => openComposer("space")} aria-label="创建社区" className="rounded-lg p-1.5 text-stone-400 hover:bg-stone-100 hover:text-stone-900"><Plus className="h-4 w-4" /></button></div>
            <button type="button" onClick={() => { setSelectedSpaceSlug(""); setSelectedPost(null); }} className={clsx("flex w-full items-center justify-between rounded-xl px-3 py-2.5 text-left text-sm font-semibold", !selectedSpaceSlug ? "bg-stone-950 text-white" : "text-stone-600 hover:bg-stone-100")}><span>全部讨论</span><span className={clsx("text-xs", !selectedSpaceSlug ? "text-white/60" : "text-stone-400")}>{spaces.reduce((sum, space) => sum + space.post_count, 0)}</span></button>
            <div className="mt-1 space-y-1">
              {spaces.map((space) => (
                <button key={space.id} type="button" onClick={() => { setSelectedSpaceSlug(space.slug); setSelectedPost(null); setTagFilter(""); }} className={clsx("flex w-full items-center gap-2 rounded-xl px-3 py-2.5 text-left text-sm transition", selectedSpaceSlug === space.slug ? "bg-[#ffe8f0] font-semibold text-[#b83965]" : "text-stone-600 hover:bg-stone-100 hover:text-stone-950")}><span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-stone-100"><Hash className="h-3.5 w-3.5" /></span><span className="min-w-0 flex-1 truncate">{space.name}</span><span className="text-xs text-stone-400">{space.post_count}</span></button>
              ))}
            </div>
            {!spaces.length ? <p className="px-3 py-6 text-center text-xs leading-5 text-stone-500">还没有主题社区。创建第一个长期学习空间。</p> : null}
            <button type="button" onClick={() => openComposer("post")} className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-xl border border-stone-200 bg-[#faf9f6] px-3 py-2.5 text-sm font-semibold text-stone-700 transition hover:border-stone-400"><Plus className="h-4 w-4" />发布内容</button>
          </div>
        </aside>

        <section className="min-w-0">
          {selectedPost ? (
            <PostDetailView detail={selectedPost} onBack={() => setSelectedPost(null)} onVote={(value) => void handleVote(value)} onComment={handleComment} />
          ) : (
            <>
              <div className="rounded-2xl border border-stone-200 bg-[linear-gradient(135deg,#fff_0%,#fff8ea_62%,#ffe9f1_100%)] p-6 shadow-[0_10px_34px_rgba(28,25,23,0.05)] sm:p-8">
                <div className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
                  <div><p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-[#d74f7b]"><Sparkles className="h-3.5 w-3.5" />Community knowledge</p><h1 className="mt-3 text-3xl font-semibold tracking-tight text-stone-950">{activeSpace?.name ?? "和正在学习的人一起思考"}</h1><p className="mt-3 max-w-2xl text-sm leading-6 text-stone-600">{activeSpace?.description || "提出问题、分享资料和学习过程，让一次交流成为可以继续使用的公共知识。"}</p></div>
                  {activeSpace ? <button type="button" disabled={followedSpaceIds.has(activeSpace.id)} onClick={() => void handleFollow(activeSpace)} className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl border border-stone-300 bg-white px-4 py-2.5 text-sm font-semibold text-stone-800 disabled:border-emerald-200 disabled:bg-emerald-50 disabled:text-emerald-700"><UsersRound className="h-4 w-4" />{followedSpaceIds.has(activeSpace.id) ? "已关注" : "关注社区"}</button> : null}
                </div>
              </div>

              <div className="mt-5 flex flex-col gap-3 rounded-2xl border border-stone-200 bg-white p-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex gap-1 overflow-x-auto">
                  {feedSorts.map(({ id, label, icon: Icon }) => <button key={id} type="button" onClick={() => setFeedSort(id)} className={clsx("inline-flex shrink-0 items-center gap-1.5 rounded-xl px-3 py-2 text-sm font-semibold transition", feedSort === id ? "bg-stone-950 text-white" : "text-stone-500 hover:bg-stone-100 hover:text-stone-900")}><Icon className="h-4 w-4" />{label}</button>)}
                </div>
                {tagFilter || query ? <button type="button" onClick={() => { setTagFilter(""); setQuery(""); setSearchInput(""); }} className="inline-flex items-center gap-1.5 self-start rounded-lg bg-stone-100 px-3 py-2 text-xs font-semibold text-stone-600"><X className="h-3.5 w-3.5" />清除筛选{tagFilter ? `：#${tagFilter}` : ""}</button> : null}
              </div>

              {error ? <p role="alert" className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</p> : null}
              <div className="mt-4 space-y-3">
                {loading ? <div className="rounded-2xl border border-stone-200 bg-white px-5 py-16 text-center text-sm text-stone-500">正在加载社区内容…</div> : null}
                {!loading && posts.map((post) => <PostCard key={post.id} post={post} onOpen={() => void handleOpenPost(post.id)} onTag={(tag) => setTagFilter(tag)} />)}
                {!loading && !posts.length ? <div className="rounded-2xl border border-dashed border-stone-300 bg-white/70 px-6 py-16 text-center"><BookOpen className="mx-auto h-8 w-8 text-stone-300" /><h2 className="mt-4 text-lg font-semibold text-stone-900">这里还没有内容</h2><p className="mt-2 text-sm text-stone-500">发布第一个问题、讨论、资料或学习记录。</p><button type="button" onClick={() => openComposer("post")} className="mt-5 rounded-xl bg-stone-950 px-4 py-2.5 text-sm font-semibold text-white">发布第一篇帖子</button></div> : null}
              </div>
            </>
          )}
        </section>

        <aside className="hidden min-w-0 xl:block">
          <div className="sticky top-22 space-y-4">
            <section className="rounded-2xl border border-stone-200 bg-white p-5"><h2 className="text-sm font-semibold text-stone-950">社区如何沉淀知识</h2><ol className="mt-4 space-y-3 text-sm leading-6 text-stone-600"><li className="flex gap-3"><span className="font-bold text-[#d74f7b]">01</span><span>用主题社区形成长期共同语境</span></li><li className="flex gap-3"><span className="font-bold text-[#d74f7b]">02</span><span>用标签连接跨社区的相关内容</span></li><li className="flex gap-3"><span className="font-bold text-[#d74f7b]">03</span><span>用投票和讨论让可靠内容自然浮现</span></li></ol></section>
            <section className="rounded-2xl border border-stone-200 bg-white p-5"><h2 className="flex items-center gap-2 text-sm font-semibold text-stone-950"><Hash className="h-4 w-4" />当前热门标签</h2><div className="mt-4 flex flex-wrap gap-2">{popularTags.map(({ tag, count }) => <button key={tag} type="button" onClick={() => { setTagFilter(tag); setSelectedPost(null); }} className={clsx("rounded-full border px-3 py-1.5 text-xs font-medium transition", tagFilter === tag ? "border-[#e37098] bg-[#ffe8f0] text-[#b83965]" : "border-stone-200 bg-stone-50 text-stone-600 hover:border-stone-400")}>#{tag} <span className="ml-1 text-stone-400">{count}</span></button>)}</div>{!popularTags.length ? <p className="mt-4 text-xs leading-5 text-stone-500">标签会随着真实帖子自动出现。</p> : null}</section>
          </div>
        </aside>
      </div>

      {composer ? <ComposerPanel kind={composer} spaces={spaces} initialCommunitySlug={selectedSpaceSlug} onClose={() => setComposer(null)} onCreated={() => void handleCreated()} /> : null}
    </main>
  );
}
