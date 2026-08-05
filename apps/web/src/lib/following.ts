export type FollowedCreator = {
  id: string;
  name: string;
  handle: string;
  bio: string;
  field: string;
  followers: number;
  avatarSeed: string;
  unreadCount: number;
};

export type FollowedCourseUpdate = {
  id: string;
  creatorId: string;
  courseTitle: string;
  moduleTitle: string;
  summary: string;
  updatedAt: string;
  updateKind: "new_lesson" | "course_revision" | "note_added";
  lessonCount: number;
  views: number;
  comments: number;
  likes: number;
  tags: string[];
  coverSeed: string;
};

export type FollowedCourseUpdateItem = {
  update: FollowedCourseUpdate;
  creator: FollowedCreator;
};

export const FOLLOWED_UPDATE_KIND_LABELS: Record<FollowedCourseUpdate["updateKind"], string> = {
  new_lesson: "New lesson",
  course_revision: "Updated",
  note_added: "New note",
};

export const FOLLOWED_CREATORS: FollowedCreator[] = [
  {
    id: "case-lab",
    name: "Case Analysis Lab",
    handle: "case-lab",
    bio: "Turn cases and classroom questions into transferable learning paths.",
    field: "Case studies",
    followers: 128400,
    avatarSeed: "case-lab",
    unreadCount: 3,
  },
  {
    id: "lesson-map",
    name: "Lesson Map",
    handle: "lesson-map",
    bio: "Organize course topics into outlines, explanation paths, and review plans.",
    field: "Lesson design",
    followers: 84200,
    avatarSeed: "source-map",
    unreadCount: 1,
  },
  {
    id: "project-studio",
    name: "Project Studio",
    handle: "project-studio",
    bio: "Break complex tasks into goals, steps, deliverables, and reviews.",
    field: "Project learning",
    followers: 96300,
    avatarSeed: "project-studio",
    unreadCount: 2,
  },
  {
    id: "pen-review",
    name: "Note Review Lab",
    handle: "pen-review",
    bio: "Explore study tools, note-taking methods, and source organization workflows.",
    field: "Learning methods",
    followers: 67200,
    avatarSeed: "pen-review",
    unreadCount: 1,
  },
  {
    id: "practice-daily",
    name: "Daily Practice Workshop",
    handle: "practice-daily",
    bio: "Turn any topic into actionable daily exercises.",
    field: "Practice design",
    followers: 73900,
    avatarSeed: "practice-daily",
    unreadCount: 0,
  },
  {
    id: "concept-bridge",
    name: "Concept Bridge",
    handle: "concept-bridge",
    bio: "Connect abstract concepts through definitions, relationships, examples, and knowledge checks.",
    field: "Concept learning",
    followers: 101800,
    avatarSeed: "concept-bridge",
    unreadCount: 4,
  },
];

export const FOLLOWED_COURSE_UPDATES: FollowedCourseUpdate[] = [
  {
    id: "case-lab-01",
    creatorId: "case-lab",
    courseTitle: "Case Analysis Course Package",
    moduleTitle: "New: From Source Facts to Supported Conclusions",
    summary: "Three adaptable cases break down fact extraction, rule mapping, and conclusion writing, with a reusable analysis map.",
    updatedAt: "2026-04-26T08:30:00.000+08:00",
    updateKind: "new_lesson",
    lessonCount: 18,
    views: 7440,
    comments: 42,
    likes: 610,
    tags: ["Cases", "Reasoning", "Transfer"],
    coverSeed: "case-analysis-path",
  },
  {
    id: "concept-bridge-01",
    creatorId: "concept-bridge",
    courseTitle: "Concept Explanation Course Package",
    moduleTitle: "Updated: Five Common Misconceptions About Concept Relationships",
    summary: "Definitions, conditions, and counterexamples appear together for quick review and self-checking.",
    updatedAt: "2026-04-26T07:40:00.000+08:00",
    updateKind: "course_revision",
    lessonCount: 24,
    views: 5820,
    comments: 31,
    likes: 428,
    tags: ["Concepts", "Relationships", "Review"],
    coverSeed: "concept-map-update",
  },
  {
    id: "project-studio-01",
    creatorId: "project-studio",
    courseTitle: "Project Learning Course Package",
    moduleTitle: "New: From Project Goals to a Delivery Checklist",
    summary: "Connect goals, constraints, and steps so deliverables and review criteria evolve together.",
    updatedAt: "2026-04-25T21:18:00.000+08:00",
    updateKind: "new_lesson",
    lessonCount: 31,
    views: 9100,
    comments: 67,
    likes: 820,
    tags: ["Projects", "Steps", "Review"],
    coverSeed: "project-delivery-system",
  },
  {
    id: "pen-review-01",
    creatorId: "pen-review",
    courseTitle: "Efficient Notes and Lesson Organization",
    moduleTitle: "Class Notes: Reading Annotation Templates",
    summary: "Three reusable reading templates cover concept cards, key excerpts, and review question lists.",
    updatedAt: "2026-04-25T18:06:00.000+08:00",
    updateKind: "note_added",
    lessonCount: 12,
    views: 4860,
    comments: 19,
    likes: 306,
    tags: ["Notes", "Templates", "Review"],
    coverSeed: "paper-note-kit",
  },
  {
    id: "lesson-map-01",
    creatorId: "lesson-map",
    courseTitle: "Source-Grounded Lesson Package",
    moduleTitle: "Class Notes: From Core Questions to an Explanation Path",
    summary: "The outline, key questions, and source tables are organized with new prompts for classroom discussion.",
    updatedAt: "2026-04-25T14:12:00.000+08:00",
    updateKind: "note_added",
    lessonCount: 9,
    views: 3520,
    comments: 26,
    likes: 211,
    tags: ["Lessons", "Sources", "Discussion"],
    coverSeed: "lesson-context-data",
  },
  {
    id: "practice-daily-01",
    creatorId: "practice-daily",
    courseTitle: "Daily Practice Course Package",
    moduleTitle: "Updated: Day 18 Scenarios and Review Questions",
    summary: "Adds adaptable practice scenarios, step-by-step explanations, keyword cards, and 12 transfer tasks.",
    updatedAt: "2026-04-24T22:05:00.000+08:00",
    updateKind: "course_revision",
    lessonCount: 18,
    views: 6290,
    comments: 38,
    likes: 472,
    tags: ["Practice", "Scenarios", "Transfer"],
    coverSeed: "practice-day-18",
  },
  {
    id: "case-lab-02",
    creatorId: "case-lab",
    courseTitle: "Structured Writing Course Package",
    moduleTitle: "New: Turn Source Facts into Structured Answers",
    summary: "Use a rubric to design the answer structure, then work through fact extraction, evidence mapping, and conclusion writing.",
    updatedAt: "2026-04-24T10:35:00.000+08:00",
    updateKind: "new_lesson",
    lessonCount: 15,
    views: 6880,
    comments: 35,
    likes: 540,
    tags: ["Writing", "Structure", "Rubrics"],
    coverSeed: "structured-answer-writing",
  },
];

export function creatorAvatarUrl(creator: FollowedCreator) {
  return `https://api.dicebear.com/9.x/glass/svg?seed=${encodeURIComponent(creator.avatarSeed)}`;
}

export function updateCoverUrl(update: FollowedCourseUpdate) {
  return `https://api.dicebear.com/9.x/shapes/svg?seed=${encodeURIComponent(update.coverSeed)}`;
}

export function buildFollowedCourseUpdateItems(): FollowedCourseUpdateItem[] {
  const creatorById = new Map(FOLLOWED_CREATORS.map((creator) => [creator.id, creator]));

  return FOLLOWED_COURSE_UPDATES.map((update) => {
    const creator = creatorById.get(update.creatorId);
    return creator ? { update, creator } : null;
  })
    .filter((item): item is FollowedCourseUpdateItem => item !== null)
    .sort((left, right) => new Date(right.update.updatedAt).getTime() - new Date(left.update.updatedAt).getTime());
}
