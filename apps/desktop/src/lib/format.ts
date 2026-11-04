const dt = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" });
export const fmtDateTime = (v?: string | null): string => (v ? dt.format(new Date(v)) : "—");
export function fmtBytes(n?: number | null): string {
  if (n === null || n === undefined) return "unknown";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(i ? 1 : 0)} ${u[i]}`;
}
export const ROLE_LABEL: Record<string, string> = {
  firm_owner: "Organization owner", practice_admin: "Administrator", practice_manager: "Practice manager", accountant: "Accountant",
  assistant_accountant: "Assistant accountant", staff: "Staff", document_operator: "Document operator", read_only: "Read-only user",
};
