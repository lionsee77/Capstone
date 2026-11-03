/** A concept's level (services, categories, ...) as a small mono tag. */
export function LevelTag({ level }: { level: string }) {
  return <span className="label-caps rounded-sm border border-rule px-1.5 py-0.5">{level}</span>;
}
