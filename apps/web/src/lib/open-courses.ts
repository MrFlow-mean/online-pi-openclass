export type OpenCourse = {
  id: string;
  owner: string;
  title: string;
  summary: string;
  topics: string[];
  language: string;
  languageColor: string;
  category: string;
  level: string;
  lessons: number;
  stars: number;
  forks: number;
  watchers: number;
  updatedAt: string;
  license: string;
  avatarSeed: string;
};

export type OpenCourseSort = "best-match" | "stars" | "updated";

export const OPEN_COURSE_COLLECTION_STORAGE_KEY = "blackboard-ai:collected-open-courses";
export const DEFAULT_COLLECTED_COURSE_IDS = ["concept-explainer", "source-handout", "practice-builder"];

export const OPEN_SOURCE_COURSES: OpenCourse[] = [
  {
    id: "concept-explainer",
    owner: "openlearn-cn",
    title: "concept-explanation-kit",
    summary: "An open course package that builds a learning path from guiding questions and clear definitions to examples and knowledge checks.",
    topics: ["concept", "example", "knowledge-map", "lesson"],
    language: "Course structure",
    languageColor: "#2563eb",
    category: "Concept explanation",
    level: "Beginner to advanced",
    lessons: 42,
    stars: 36400,
    forks: 3100,
    watchers: 824,
    updatedAt: "2026-04-25T08:18:00.000Z",
    license: "CC BY-SA 4.0",
    avatarSeed: "concept-explainer",
  },
  {
    id: "structured-handout",
    owner: "coursecraft",
    title: "structured-lesson-workflow",
    summary: "A complete workflow to generate course scripts, lesson documents, class questions, and review tests around learning objectives.",
    topics: ["teaching", "lesson", "workflow"],
    language: "Lesson design",
    languageColor: "#16a34a",
    category: "Course design",
    level: "Practical",
    lessons: 28,
    stars: 18200,
    forks: 1260,
    watchers: 438,
    updatedAt: "2026-04-22T16:40:00.000Z",
    license: "MIT",
    avatarSeed: "source-handout",
  },
  {
    id: "project-workshop",
    owner: "workflow-labs",
    title: "project-practice-course",
    summary: "Build reusable project-based courses that connect goals, constraints, steps, and deliverables.",
    topics: ["project", "workflow", "delivery", "review"],
    language: "Project learning",
    languageColor: "#3178c6",
    category: "Project learning",
    level: "Advanced",
    lessons: 36,
    stars: 27100,
    forks: 2400,
    watchers: 516,
    updatedAt: "2026-04-24T10:02:00.000Z",
    license: "Apache-2.0",
    avatarSeed: "project-workshop",
  },
  {
    id: "practice-builder",
    owner: "practicestack",
    title: "transfer-practice-lab",
    summary: "A practice course package that uses interactive tasks to develop concepts, procedures, transfer, and reflection.",
    topics: ["practice", "exercise", "transfer", "feedback"],
    language: "Practice design",
    languageColor: "#7c3aed",
    category: "Practice",
    level: "Advanced",
    lessons: 31,
    stars: 15500,
    forks: 980,
    watchers: 302,
    updatedAt: "2026-04-20T09:12:00.000Z",
    license: "CC BY 4.0",
    avatarSeed: "practice-builder",
  },
  {
    id: "data-story",
    owner: "dataschool",
    title: "data-to-explanation-open",
    summary: "Open courses for organizing data, interpreting evidence, and building visual reports.",
    topics: ["data", "visualization", "explanation", "report"],
    language: "Data explanation",
    languageColor: "#3776ab",
    category: "Data expression",
    level: "Beginner",
    lessons: 33,
    stars: 22900,
    forks: 2130,
    watchers: 481,
    updatedAt: "2026-04-23T13:27:00.000Z",
    license: "BSD-3-Clause",
    avatarSeed: "data-story",
  },
  {
    id: "case-analysis",
    owner: "buildbetter",
    title: "case-analysis-path",
    summary: "Use adaptable cases to practice fact extraction, evidence mapping, step-by-step reasoning, and clear conclusions.",
    topics: ["case-study", "reasoning", "rubric", "roadmap"],
    language: "Case study",
    languageColor: "#f97316",
    category: "case study",
    level: "Practical",
    lessons: 24,
    stars: 11900,
    forks: 760,
    watchers: 197,
    updatedAt: "2026-04-18T18:05:00.000Z",
    license: "CC BY-NC 4.0",
    avatarSeed: "case-analysis",
  },
  {
    id: "writing-studio",
    owner: "langopen",
    title: "structured-writing-studio",
    summary: "A structured writing course covering argument design, paragraph flow, quotations, and revision feedback.",
    topics: ["writing", "structure", "revision", "feedback"],
    language: "expression training",
    languageColor: "#0ea5e9",
    category: "Writing",
    level: "Intermediate",
    lessons: 26,
    stars: 9800,
    forks: 610,
    watchers: 162,
    updatedAt: "2026-04-19T11:44:00.000Z",
    license: "CC BY 4.0",
    avatarSeed: "writing-studio",
  },
  {
    id: "build-from-scratch",
    owner: "makerlab",
    title: "build-from-scratch",
    summary: "An introductory course on the path from requirements to prototype to testing, including simulation tasks and practice checklists.",
    topics: ["build", "prototype", "simulation", "checklist"],
    language: "Practical courses",
    languageColor: "#00599c",
    category: "practice",
    level: "Beginner to advanced",
    lessons: 39,
    stars: 13200,
    forks: 1040,
    watchers: 226,
    updatedAt: "2026-04-17T07:52:00.000Z",
    license: "MIT",
    avatarSeed: "build-from-scratch",
  },
];

export function formatCompactNumber(value: number) {
  if (value >= 10000) {
    return `${(value / 1000).toFixed(value >= 100000 ? 0 : 1)}K`;
  }

  if (value >= 1000) {
    return `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}k`;
  }

  return value.toLocaleString("en-US");
}

export function courseFullName(course: OpenCourse) {
  return `${course.owner}/${course.title}`;
}

export function courseDetailHref(course: Pick<OpenCourse, "id">) {
  return `/courses/${course.id}`;
}

export function courseAvatarUrl(course: OpenCourse) {
  return `https://api.dicebear.com/9.x/glass/svg?seed=${encodeURIComponent(course.avatarSeed)}`;
}

export function searchOpenCourses(query: string) {
  const terms = query
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);

  if (!terms.length) {
    return OPEN_SOURCE_COURSES;
  }

  return OPEN_SOURCE_COURSES.filter((course) => {
    const searchable = [
      course.owner,
      course.title,
      course.summary,
      course.category,
      course.level,
      course.language,
      course.license,
      course.topics.join(" "),
    ]
      .join(" ")
      .toLowerCase();

    return terms.every((term) => searchable.includes(term));
  });
}

export function sortOpenCourses(courses: OpenCourse[], sort: OpenCourseSort) {
  const sorted = [...courses];

  if (sort === "stars") {
    return sorted.sort((left, right) => right.stars - left.stars);
  }

  if (sort === "updated") {
    return sorted.sort((left, right) => new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime());
  }

  return sorted.sort((left, right) => {
    const scoreLeft = left.stars * 0.7 + left.watchers * 12 + left.lessons * 100;
    const scoreRight = right.stars * 0.7 + right.watchers * 12 + right.lessons * 100;
    return scoreRight - scoreLeft;
  });
}
