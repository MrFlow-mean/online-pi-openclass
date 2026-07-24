"use client";

import clsx from "clsx";
import { ArrowDown, ArrowLeft, ArrowUp, CheckCircle2, CircleHelp, Edit3, MessageCircle, Send, Trash2, X } from "lucide-react";
import { FormEvent, useState } from "react";

import { clearCommunityDraft, CommunityEditor } from "@/components/community/community-editor";
import { CommunityMarkdown } from "@/components/community/community-markdown";
import type { CommunityAnswer, CommunityPostDetail, UpdateCommunityPostPayload } from "@/types";


function relativeTime(value: string) {
  const timestamp = new Date(value).getTime();
  const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60_000));
  if (!Number.isFinite(minutes) || minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前`;
  return `${Math.floor(minutes / 1440)} 天前`;
}


function ContentActions({ canManage, onEdit, onDelete }: { canManage: boolean; onEdit: () => void; onDelete: () => void }) {
  if (!canManage) return null;
  return (
    <div className="flex items-center gap-1">
      <button type="button" onClick={onEdit} className="rounded-lg p-2 text-stone-400 hover:bg-stone-100 hover:text-stone-900" aria-label="编辑"><Edit3 className="h-4 w-4" /></button>
      <button type="button" onClick={onDelete} className="rounded-lg p-2 text-stone-400 hover:bg-rose-50 hover:text-rose-700" aria-label="删除"><Trash2 className="h-4 w-4" /></button>
    </div>
  );
}


function AnswerCard({ answer, viewerUserId, viewerIsAdmin, canAccept, onVote, onAccept, onEdit, onDelete }: {
  answer: CommunityAnswer;
  viewerUserId?: string;
  viewerIsAdmin: boolean;
  canAccept: boolean;
  onVote: (answerId: string, value: -1 | 0 | 1) => void;
  onAccept: (answerId: string | null) => void;
  onEdit: (answerId: string, body: string) => Promise<void>;
  onDelete: (answerId: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [body, setBody] = useState(answer.body);
  const canManage = viewerIsAdmin || viewerUserId === answer.author_user_id;
  return (
    <article className={clsx("rounded-2xl border p-4 sm:p-5", answer.is_accepted ? "border-emerald-300 bg-emerald-50/40" : "border-stone-200 bg-white")}>
      <div className="flex gap-4">
        <div className="flex w-9 shrink-0 flex-col items-center gap-1">
          <button type="button" aria-label={`赞同 ${answer.author_display_name} 的回答`} onClick={() => onVote(answer.id, answer.viewer_vote === 1 ? 0 : 1)} className={clsx("rounded-lg p-1.5", answer.viewer_vote === 1 ? "bg-[#ffe7ef] text-[#d74f7b]" : "text-stone-400")}><ArrowUp className="h-5 w-5" /></button>
          <span className="text-sm font-bold">{answer.vote_score}</span>
          <button type="button" aria-label={`不赞同 ${answer.author_display_name} 的回答`} onClick={() => onVote(answer.id, answer.viewer_vote === -1 ? 0 : -1)} className={clsx("rounded-lg p-1.5", answer.viewer_vote === -1 ? "bg-sky-100 text-sky-700" : "text-stone-400")}><ArrowDown className="h-5 w-5" /></button>
          {canAccept ? <button type="button" aria-label={answer.is_accepted ? `取消采纳 ${answer.author_display_name} 的回答` : `采纳 ${answer.author_display_name} 的回答`} onClick={() => onAccept(answer.is_accepted ? null : answer.id)} className={clsx("mt-2 rounded-full p-1.5", answer.is_accepted ? "bg-emerald-600 text-white" : "border border-stone-300 text-stone-400")}><CheckCircle2 className="h-5 w-5" /></button> : answer.is_accepted ? <CheckCircle2 aria-label="已采纳" className="mt-2 h-6 w-6 text-emerald-600" /> : null}
        </div>
        <div className="min-w-0 flex-1">
          <div className="mb-2 flex items-start justify-between gap-3">
            {answer.is_accepted ? <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-semibold text-emerald-800">提问者已采纳</span> : <span />}
            <ContentActions canManage={canManage} onEdit={() => setEditing(true)} onDelete={() => { if (window.confirm("确定删除这条回答吗？")) void onDelete(answer.id); }} />
          </div>
          {editing ? <div className="space-y-3"><CommunityEditor label="编辑回答" value={body} onChange={setBody} placeholder="完善回答" /><div className="flex justify-end gap-2"><button type="button" onClick={() => { setBody(answer.body); setEditing(false); }} className="rounded-lg px-3 py-2 text-xs font-semibold text-stone-500">取消</button><button type="button" onClick={() => void onEdit(answer.id, body).then(() => setEditing(false))} className="rounded-lg bg-stone-950 px-3 py-2 text-xs font-semibold text-white">保存修改</button></div></div> : <CommunityMarkdown content={answer.body} />}
          <div className="mt-4 flex flex-wrap gap-2 text-xs text-stone-500"><span className="font-semibold text-stone-800">{answer.author_display_name}</span><span>贡献 {answer.author_reputation}</span><span>· {relativeTime(answer.created_at)}</span>{answer.updated_at !== answer.created_at ? <span>· 已编辑</span> : null}</div>
        </div>
      </div>
    </article>
  );
}


export function CommunityPostDetailView({ detail, viewerUserId, viewerIsAdmin, onBack, onVote, onAnswer, onAnswerVote, onAcceptAnswer, onComment, onEditPost, onDeletePost, onEditAnswer, onDeleteAnswer, onEditComment, onDeleteComment }: {
  detail: CommunityPostDetail;
  viewerUserId?: string;
  viewerIsAdmin: boolean;
  onBack: () => void;
  onVote: (value: -1 | 0 | 1) => void;
  onAnswer: (body: string) => Promise<void>;
  onAnswerVote: (answerId: string, value: -1 | 0 | 1) => void;
  onAcceptAnswer: (answerId: string | null) => void;
  onComment: (body: string, parentCommentId?: string | null) => Promise<void>;
  onEditPost: (payload: UpdateCommunityPostPayload) => Promise<void>;
  onDeletePost: () => Promise<void>;
  onEditAnswer: (answerId: string, body: string) => Promise<void>;
  onDeleteAnswer: (answerId: string) => Promise<void>;
  onEditComment: (commentId: string, body: string) => Promise<void>;
  onDeleteComment: (commentId: string) => Promise<void>;
}) {
  const post = detail.post;
  const [answerBody, setAnswerBody] = useState("");
  const [commentBody, setCommentBody] = useState("");
  const [replyTo, setReplyTo] = useState<{ id: string; name: string } | null>(null);
  const [editingPost, setEditingPost] = useState(false);
  const [editTitle, setEditTitle] = useState(post.title);
  const [editBody, setEditBody] = useState(post.body);
  const [editTags, setEditTags] = useState(post.tags.join(", "));
  const [editingCommentId, setEditingCommentId] = useState("");
  const [editingCommentBody, setEditingCommentBody] = useState("");
  const canManagePost = viewerIsAdmin || viewerUserId === post.author_user_id;

  async function submitAnswer(event: FormEvent) {
    event.preventDefault();
    if (!answerBody.trim()) return;
    await onAnswer(answerBody.trim());
    clearCommunityDraft(`openclass:community:answer:${post.id}`);
    setAnswerBody("");
  }

  async function submitComment(event: FormEvent) {
    event.preventDefault();
    if (!commentBody.trim()) return;
    await onComment(commentBody.trim(), replyTo?.id);
    clearCommunityDraft(`openclass:community:comment:${post.id}`);
    setCommentBody("");
    setReplyTo(null);
  }

  return (
    <section className="rounded-2xl border border-stone-200 bg-white p-5 shadow-[0_12px_34px_rgba(28,25,23,0.06)] sm:p-7">
      <div className="flex items-center justify-between gap-3"><button type="button" onClick={onBack} className="inline-flex items-center gap-2 text-sm font-semibold text-stone-500"><ArrowLeft className="h-4 w-4" />返回帖子列表</button><ContentActions canManage={canManagePost} onEdit={() => setEditingPost(true)} onDelete={() => { if (window.confirm("确定删除这篇帖子及其全部讨论吗？")) void onDeletePost(); }} /></div>
      <div className="mt-6 flex gap-4">
        <div className="flex w-10 shrink-0 flex-col items-center gap-1 rounded-xl bg-stone-50 py-2"><button type="button" aria-label="赞同帖子" onClick={() => onVote(post.viewer_vote === 1 ? 0 : 1)} className="p-1.5"><ArrowUp className="h-5 w-5" /></button><span className="text-sm font-bold">{post.vote_score}</span><button type="button" aria-label="不赞同帖子" onClick={() => onVote(post.viewer_vote === -1 ? 0 : -1)} className="p-1.5"><ArrowDown className="h-5 w-5" /></button></div>
        <div className="min-w-0 flex-1">
          <div className="text-xs text-stone-500">c/{post.community_name} · {post.author_display_name} · {relativeTime(post.created_at)}{post.updated_at !== post.created_at ? " · 已编辑" : ""}</div>
          {editingPost ? <div className="mt-4 space-y-3"><input aria-label="编辑帖子标题" value={editTitle} onChange={(event) => setEditTitle(event.target.value)} className="w-full rounded-xl border border-stone-200 px-4 py-3 text-xl font-semibold" /><CommunityEditor label="编辑帖子正文" value={editBody} onChange={setEditBody} placeholder="完善正文" /><input aria-label="编辑帖子标签" value={editTags} onChange={(event) => setEditTags(event.target.value)} className="w-full rounded-xl border border-stone-200 px-4 py-3 text-sm" /><div className="flex justify-end gap-2"><button type="button" onClick={() => setEditingPost(false)} className="px-4 py-2 text-sm">取消</button><button type="button" onClick={() => void onEditPost({ title: editTitle, body: editBody, tags: editTags.split(/[，,]/).map((tag) => tag.trim().replace(/^#/, "")).filter(Boolean) }).then(() => setEditingPost(false))} className="rounded-lg bg-stone-950 px-4 py-2 text-sm font-semibold text-white">保存修改</button></div></div> : <><h1 className="mt-3 text-2xl font-semibold text-stone-950 sm:text-3xl">{post.title}</h1><div className="mt-5"><CommunityMarkdown content={post.body} /></div><div className="mt-5 flex flex-wrap gap-2">{post.tags.map((tag) => <span key={tag} className="rounded-full bg-stone-100 px-2.5 py-1 text-xs text-stone-600">#{tag}</span>)}</div></>}
        </div>
      </div>

      {post.post_type === "question" ? <div className="mt-8 border-t border-stone-200 pt-6"><h2 className="flex items-center gap-2 text-lg font-semibold"><CircleHelp className="h-5 w-5" />{detail.answers.length} 条回答</h2><div className="mt-5 space-y-4">{detail.answers.map((answer) => <AnswerCard key={answer.id} answer={answer} viewerUserId={viewerUserId} viewerIsAdmin={viewerIsAdmin} canAccept={viewerUserId === post.author_user_id} onVote={onAnswerVote} onAccept={onAcceptAnswer} onEdit={onEditAnswer} onDelete={onDeleteAnswer} />)}</div><form onSubmit={submitAnswer} className="mt-5 space-y-3"><CommunityEditor label="写回答" value={answerBody} onChange={setAnswerBody} draftKey={`openclass:community:answer:${post.id}`} placeholder="给出清晰、可验证的回答" /><div className="flex justify-end"><button disabled={!answerBody.trim()} className="inline-flex items-center gap-2 rounded-lg bg-stone-950 px-4 py-2 text-xs font-semibold text-white disabled:opacity-40"><Send className="h-3.5 w-3.5" />提交回答</button></div></form></div> : null}

      <div className="mt-8 border-t border-stone-200 pt-6"><h2 className="flex items-center gap-2 text-lg font-semibold"><MessageCircle className="h-5 w-5" />{detail.comments.length} 条讨论</h2><form onSubmit={submitComment} className="mt-4 space-y-3">{replyTo ? <div className="flex items-center justify-between rounded-lg bg-stone-50 px-3 py-2 text-xs"><span>回复 {replyTo.name}</span><button type="button" onClick={() => setReplyTo(null)}><X className="h-3.5 w-3.5" /></button></div> : null}<CommunityEditor label="写评论" value={commentBody} onChange={setCommentBody} draftKey={`openclass:community:comment:${post.id}`} placeholder="补充信息、提出追问或继续讨论" rows={4} /><div className="flex justify-end"><button disabled={!commentBody.trim()} className="rounded-lg bg-stone-950 px-4 py-2 text-xs font-semibold text-white disabled:opacity-40">参与讨论</button></div></form><div className="mt-5 space-y-3">{detail.comments.map((comment) => { const canManage = viewerIsAdmin || viewerUserId === comment.author_user_id; return <article key={comment.id} className={clsx("rounded-xl border border-stone-200 p-4", comment.parent_comment_id && "ml-6 border-l-4")}><div className="flex items-center justify-between gap-3"><div className="text-xs text-stone-500"><span className="font-semibold text-stone-800">{comment.author_display_name}</span> · {relativeTime(comment.created_at)}{comment.updated_at !== comment.created_at ? " · 已编辑" : ""}</div><ContentActions canManage={canManage} onEdit={() => { setEditingCommentId(comment.id); setEditingCommentBody(comment.body); }} onDelete={() => { if (window.confirm("确定删除这条评论吗？")) void onDeleteComment(comment.id); }} /></div>{editingCommentId === comment.id ? <div className="mt-3 space-y-2"><CommunityEditor label="编辑评论" value={editingCommentBody} onChange={setEditingCommentBody} placeholder="修改评论" rows={4} /><div className="flex justify-end gap-2"><button type="button" onClick={() => setEditingCommentId("")} className="px-3 py-2 text-xs">取消</button><button type="button" onClick={() => void onEditComment(comment.id, editingCommentBody).then(() => setEditingCommentId(""))} className="rounded-lg bg-stone-950 px-3 py-2 text-xs text-white">保存</button></div></div> : <div className="mt-2"><CommunityMarkdown compact content={comment.body} /></div>}<button type="button" onClick={() => setReplyTo({ id: comment.id, name: comment.author_display_name })} className="mt-3 text-xs font-semibold text-stone-500">回复</button></article>; })}</div></div>
    </section>
  );
}
