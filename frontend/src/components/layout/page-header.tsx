import { cn } from "@/lib/utils";

/** The heading block every page opens with: a small-caps eyebrow, a serif
 * title, an optional description and actions, and a rule underneath. */
export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  titleClassName,
}: {
  eyebrow: string;
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  titleClassName?: string;
}) {
  return (
    <header className="flex flex-col gap-3 border-b border-rule pb-6">
      <p className="label-caps">{eyebrow}</p>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h1 className={cn("text-3xl leading-tight font-medium md:text-4xl", titleClassName)}>{title}</h1>
        {actions && <div className="flex items-center gap-3">{actions}</div>}
      </div>
      {description && <p className="max-w-prose text-sm text-muted-foreground">{description}</p>}
    </header>
  );
}

/** A titled block of a page, separated by a hairline rule instead of a card border. */
export function Section({
  title,
  aside,
  children,
  className,
}: {
  title: React.ReactNode;
  aside?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("flex flex-col gap-3", className)}>
      <div className="flex items-baseline justify-between gap-4 border-b border-rule pb-2">
        <h2 className="text-lg font-medium">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

/** A quiet message for empty, missing or failed states: a serif line plus help text. */
export function Notice({
  title,
  children,
  tone = "muted",
}: {
  title: string;
  children?: React.ReactNode;
  tone?: "muted" | "error";
}) {
  return (
    <div
      role={tone === "error" ? "alert" : undefined}
      className={cn(
        "flex flex-col items-start gap-1.5 border-l-2 py-1 pl-4",
        tone === "error" ? "border-destructive" : "border-rule",
      )}
    >
      <p className={cn("font-heading text-base", tone === "error" && "text-destructive")}>{title}</p>
      {children && <div className="text-sm text-muted-foreground">{children}</div>}
    </div>
  );
}
