import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { notFound } from "next/navigation";
import {
  ArrowLeft,
  BookOpenCheck,
  Clock3,
  GitFork,
  GraduationCap,
  Star,
  UsersRound,
} from "lucide-react";

import {
  OPEN_SOURCE_COURSES,
  courseAvatarUrl,
  courseDetailHref,
  courseFullName,
  formatCompactNumber,
} from "@/lib/open-courses";

type CourseDetailPageProps = {
  params: Promise<{
    courseId: string;
  }>;
};

function findCourse(courseId: string) {
  return OPEN_SOURCE_COURSES.find((course) => course.id === courseId);
}

function formatUpdatedAt(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
  }).format(new Date(value));
}

export function generateStaticParams() {
  return OPEN_SOURCE_COURSES.map((course) => ({ courseId: course.id }));
}

export async function generateMetadata({ params }: CourseDetailPageProps): Promise<Metadata> {
  const { courseId } = await params;
  const course = findCourse(courseId);

  if (!course) {
    return {
      title: "Course does not exist",
    };
  }

  return {
    title: courseFullName(course),
    description: course.summary,
  };
}

export default async function CourseDetailPage({ params }: CourseDetailPageProps) {
  const { courseId } = await params;
  const course = findCourse(courseId);

  if (!course) {
    notFound();
  }

  const stats = [
    { label: "Stars", value: formatCompactNumber(course.stars), Icon: Star },
    { label: "Forks", value: formatCompactNumber(course.forks), Icon: GitFork },
    { label: "Lessons", value: course.lessons.toString(), Icon: GraduationCap },
    { label: "Watchers", value: formatCompactNumber(course.watchers), Icon: UsersRound },
  ];

  return (
    <main className="min-h-screen bg-[#f7f5ef] text-stone-950">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-8 px-4 py-6 sm:px-6 lg:px-8">
        <Link
          href="/trending"
          className="inline-flex w-fit items-center gap-2 rounded-md border border-stone-200 bg-white px-3 py-2 text-sm font-semibold text-stone-700 transition hover:border-stone-300 hover:text-stone-950"
        >
          <ArrowLeft className="h-4 w-4" />

          Return to OpenClass
        </Link>

        <section className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <div className="min-w-0">
            <div className="flex flex-col gap-5 sm:flex-row sm:items-start">
              <Image
                src={courseAvatarUrl(course)}
                alt=""
                className="h-20 w-20 rounded-lg border border-stone-200 bg-white"
                width={80}
                height={80}
                priority
                unoptimized
              />
              <div className="min-w-0">
                <p className="text-sm font-semibold text-stone-500">{course.owner}</p>
                <h1 className="mt-2 break-words text-4xl font-semibold tracking-tight text-stone-950 sm:text-5xl">
                  {course.title}
                </h1>
                <p className="mt-5 max-w-3xl text-base leading-8 text-stone-700">{course.summary}</p>
              </div>
            </div>

            <div className="mt-8 flex flex-wrap items-center gap-2">
              <span
                className="inline-flex items-center gap-2 rounded-full border border-stone-200 bg-white px-3 py-1.5 text-sm font-semibold text-stone-700"
                style={{ color: course.languageColor }}
              >
                <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: course.languageColor }} />
                {course.language}
              </span>
              <span className="rounded-full border border-stone-200 bg-white px-3 py-1.5 text-sm font-semibold text-stone-700">
                {course.category}
              </span>
              <span className="rounded-full border border-stone-200 bg-white px-3 py-1.5 text-sm font-semibold text-stone-700">
                {course.level}
              </span>
              <span className="rounded-full border border-stone-200 bg-white px-3 py-1.5 text-sm font-semibold text-stone-700">
                {course.license}
              </span>
            </div>
          </div>

          <aside className="h-fit rounded-lg border border-stone-200 bg-white p-5 shadow-[0_16px_34px_rgba(15,23,42,0.05)]">
            <div className="flex items-center gap-2 text-sm font-semibold text-stone-950">
              <BookOpenCheck className="h-4 w-4 text-sky-600" />

              Course Overview
            </div>
            <dl className="mt-5 grid grid-cols-2 gap-3">
              {stats.map(({ label, value, Icon }) => (
                <div key={label} className="rounded-md border border-stone-200 bg-stone-50 px-3 py-3">
                  <dt className="flex items-center gap-1.5 text-xs text-stone-500">
                    <Icon className="h-3.5 w-3.5" />
                    {label}
                  </dt>
                  <dd className="mt-2 text-lg font-semibold text-stone-950">{value}</dd>
                </div>
              ))}
            </dl>
            <div className="mt-5 flex items-center gap-2 rounded-md border border-stone-200 bg-white px-3 py-3 text-sm text-stone-600">
              <Clock3 className="h-4 w-4 text-stone-400" />

              updated on {formatUpdatedAt(course.updatedAt)}
            </div>
          </aside>
        </section>

        <section className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <div className="rounded-lg border border-stone-200 bg-white p-5 shadow-[0_16px_34px_rgba(15,23,42,0.05)]">
            <h2 className="text-base font-semibold text-stone-950">Course Topics</h2>
            <div className="mt-4 flex flex-wrap gap-2">
              {course.topics.map((topic) => (
                <span key={topic} className="rounded-full bg-sky-50 px-3 py-1.5 text-sm font-semibold text-sky-700">
                  {topic}
                </span>
              ))}
            </div>
          </div>

          <div className="rounded-lg border border-stone-200 bg-white p-5 shadow-[0_16px_34px_rgba(15,23,42,0.05)]">
            <h2 className="text-base font-semibold text-stone-950">access path</h2>
            <p className="mt-3 break-all font-mono text-sm text-stone-500">{courseDetailHref(course)}</p>
          </div>
        </section>
      </div>
    </main>
  );
}
