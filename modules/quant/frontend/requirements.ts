/* The user's package list (requirements.txt in the common code folder): one pip requirement per line, comments kept. */

/** A package's name as pip compares it: case and the -_. separators do not matter. */
export const packageName = (requirement: string) => requirement.split(/[\s<>=!~;[@]/, 1)[0].replace(/[-_.]+/g, "-").toLowerCase();

/** What is wrong with a requirement typed into the list, or null. Options and files are not packages. */
export function requirementProblem(requirement: string, current: string[]): string | null {
  const text = requirement.trim();
  if (!text) return "Type a package name.";
  if (text.startsWith("-") || text.startsWith("#") || /\s/.test(text.replace(/\s*([<>=!~;,]=?|===?)\s*/g, "$1")))
    return "One package per line, as pip writes it: pandas-ta or ta>=0.11.";
  if (!/^[A-Za-z0-9]/.test(text)) return "A package name starts with a letter or digit.";
  if (current.some(item => packageName(item) === packageName(text))) return `${packageName(text)} is already in the list.`;
  return null;
}

/** The file with a requirement added at the end. */
export function addRequirement(content: string, requirement: string): string {
  const body = content.endsWith("\n") || content === "" ? content : content + "\n";
  return body + requirement.trim() + "\n";
}

/** The file without the lines naming this package; comments and other lines stay as they were. */
export function removeRequirement(content: string, requirement: string): string {
  const name = packageName(requirement);
  return content.split(/(?<=\n)/).filter(line => {
    const text = line.split("#", 1)[0].trim();
    return !text || packageName(text) !== name;
  }).join("");
}
