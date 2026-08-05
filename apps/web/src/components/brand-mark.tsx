import clsx from "clsx";
import Image from "next/image";

type BrandMarkProps = {
  alt?: string;
  className?: string;
  priority?: boolean;
  size?: number;
};

export function BrandMark({ alt = "OpenClass logo", className, priority = false, size = 64 }: BrandMarkProps) {
  return (
    <span className={clsx("inline-flex shrink-0 items-center justify-center overflow-hidden", className)}>
      <Image
        src="/openclass-mark.png"
        alt={alt}
        width={size}
        height={size}
        className="h-full w-full object-contain"
        priority={priority}
      />
    </span>
  );
}
