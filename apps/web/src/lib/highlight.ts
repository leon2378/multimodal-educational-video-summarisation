/** Words too common to be why a passage matched. */
const STOP = new Set(
  "the and are but can did does for from had has have her his how its not our she that their them then there these they this was were what when where which who why will with you your just into about".split(
    " ",
  ),
);

/** Splits `text` into pieces, marking the words of `query` it contains (whole or as a prefix,
 *  any case), so search results can show why they matched. Short and common words are skipped:
 *  "of" or "the" would mark half the passage. */
export function highlight(text: string, query: string): { text: string; match: boolean }[] {
  const terms = query.toLowerCase().match(/[\p{L}\p{N}]{3,}/gu) ?? [];
  const words = [...new Set(terms.filter((word) => !STOP.has(word)))];
  if (words.length === 0) return [{ text, match: false }];
  const escaped = words.map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(`(?<![\\p{L}\\p{N}])(${escaped.join("|")})[\\p{L}\\p{N}]*`, "giu");
  const pieces: { text: string; match: boolean }[] = [];
  let last = 0;
  for (const found of text.matchAll(pattern)) {
    const start = found.index;
    if (start > last) pieces.push({ text: text.slice(last, start), match: false });
    pieces.push({ text: found[0], match: true });
    last = start + found[0].length;
  }
  if (last < text.length) pieces.push({ text: text.slice(last), match: false });
  return pieces;
}
