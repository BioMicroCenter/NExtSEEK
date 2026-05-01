import type { Root, Text, Link, PhrasingContent } from "mdast";
import type { Plugin } from "unified";
import { visit } from "unist-util-visit";

/**
 * Determine the URL for a matched UID string.
 * SOP UIDs (P. prefix) → /seek/sop/uid=.../
 * Sample UIDs           → /seek/sampletree/uid=.../
 */
function getUrlForUid(uid: string): string {
  if (uid.startsWith("P.")) {
    return `/seek/sop/uid=${encodeURIComponent(uid)}/`;
  }
  return `/seek/sampletree/uid=${encodeURIComponent(uid)}/`;
}

// Combined regex that matches either SOP or sample UIDs.
// SOP pattern first (more specific due to P. prefix).
const COMBINED_REGEX =
  /\b(P\.[A-Z]{2,5}-\d{6}-V\d+_[\w.+\-]+(?:\.\w{2,5})?|(?:[AD]\.)?[A-Z]{3}-\d{6}[A-Z]{2,5}-\d+(?:-PUB\d*)?)\b/g;

/**
 * Remark plugin that converts bare NExtSEEK UIDs into markdown links.
 *
 * Sample UIDs: {PREFIX}-{YYMMDD}{LAB}-{N}[-PUB[N]]
 *   - 3-letter:       NHP-220630FLY-2
 *   - D. + 3-letter:  D.IMG-220630FLY-1
 *   - A. + 3-letter:  A.GEX-220630FLY-2
 *
 * SOP UIDs: P.{2-5 letter}-{YYMMDD}-V{n}_{filename}[.ext]
 *   - P.ESS-251028-V1_NG_8-GEX.docx
 *
 * Only processes text nodes — UIDs inside code blocks or existing links
 * are left untouched.
 */
const remarkUidLinks: Plugin<[], Root> = () => {
  return (tree: Root) => {
    visit(tree, "text", (node: Text, index, parent) => {
      if (!parent || index === undefined) return;

      // Skip text inside links (already linked) or code blocks
      const parentType = parent.type as string;
      if (
        parentType === "link" ||
        parentType === "code" ||
        parentType === "inlineCode"
      ) {
        return;
      }

      const text = node.value;
      const matches = [...text.matchAll(COMBINED_REGEX)];
      if (matches.length === 0) return;

      // Split the text node into alternating text and link nodes
      const children: PhrasingContent[] = [];
      let lastIndex = 0;

      for (const match of matches) {
        const uid = match[1];
        const start = match.index!;

        // Text before the match
        if (start > lastIndex) {
          children.push({ type: "text", value: text.slice(lastIndex, start) });
        }

        // The UID as a link
        children.push({
          type: "link",
          url: getUrlForUid(uid),
          children: [{ type: "text", value: uid }],
        } as Link);

        lastIndex = start + match[0].length;
      }

      // Remaining text after last match
      if (lastIndex < text.length) {
        children.push({ type: "text", value: text.slice(lastIndex) });
      }

      // Replace the original text node with the new children
      parent.children.splice(index, 1, ...children);
    });
  };
};

export default remarkUidLinks;
