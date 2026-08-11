import { describe, it, expect } from "vitest";
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkStringify from "remark-stringify";
import remarkUidLinks from "../remark-uid-links";

async function process(markdown: string): Promise<string> {
  const result = await unified()
    .use(remarkParse)
    .use(remarkUidLinks)
    .use(remarkStringify)
    .process(markdown);
  return String(result);
}

describe("remarkUidLinks", () => {
  it("converts a bare UID into a markdown link", async () => {
    const input = "Found sample NHP-220630FLY-1-PUB in results.";
    const output = await process(input);
    expect(output).toContain("[NHP-220630FLY-1-PUB](/seek/sampletree/uid=NHP-220630FLY-1-PUB/)");
  });

  it("converts multiple UIDs in the same line", async () => {
    const input = "Samples: NHP-220630FLY-1-PUB, TIS-230324BOO-39-PUB";
    const output = await process(input);
    expect(output).toContain("[NHP-220630FLY-1-PUB]");
    expect(output).toContain("[TIS-230324BOO-39-PUB]");
  });

  it("handles UIDs without -PUB suffix", async () => {
    const input = "Draft sample PAV-230522GRI-7 found.";
    const output = await process(input);
    expect(output).toContain("[PAV-230522GRI-7](/seek/sampletree/uid=PAV-230522GRI-7/)");
  });

  it("handles D. prefix UIDs", async () => {
    const input = "Data: D.IMG-220630FLY-1-PUB";
    const output = await process(input);
    expect(output).toContain("[D.IMG-220630FLY-1-PUB]");
  });

  it("handles A. prefix UIDs", async () => {
    const input = "Assay: A.GEX-220630FLY-2-PUB";
    const output = await process(input);
    expect(output).toContain("[A.GEX-220630FLY-2-PUB]");
  });

  it("handles two-letter AB UIDs", async () => {
    const input = "Antibody AB-230522GRI-1 used.";
    const output = await process(input);
    expect(output).toContain("[AB-230522GRI-1](/seek/sampletree/uid=AB-230522GRI-1/)");
  });

  it("handles M. prefix UIDs", async () => {
    const input = "Model M.LMM-231208ALT-1 sample.";
    const output = await process(input);
    expect(output).toContain("[M.LMM-231208ALT-1](/seek/sampletree/uid=M.LMM-231208ALT-1/)");
  });

  it("handles -PUB with integer suffix", async () => {
    const input = "Published: NHP-220630FLY-1-PUB1";
    const output = await process(input);
    expect(output).toContain("[NHP-220630FLY-1-PUB1](/seek/sampletree/uid=NHP-220630FLY-1-PUB1/)");
  });

  it("does NOT linkify text inside code blocks", async () => {
    const input = "```\nNHP-220630FLY-1-PUB\n```";
    const output = await process(input);
    expect(output).not.toContain("[NHP-220630FLY-1-PUB]");
  });

  it("does NOT linkify text inside inline code", async () => {
    const input = "The UID is `NHP-220630FLY-1-PUB` here.";
    const output = await process(input);
    expect(output).toContain("`NHP-220630FLY-1-PUB`");
    expect(output).not.toMatch(/\[NHP-220630FLY-1-PUB\]\(/);
  });

  it("does NOT double-linkify UIDs already in links", async () => {
    const input = "See [NHP-220630FLY-1-PUB](http://example.com)";
    const output = await process(input);
    expect(output).toContain("(http://example.com)");
  });

  it("does NOT match strings that are not UIDs", async () => {
    const input = "Version ABC-1234 is not a UID. Neither is hello-world.";
    const output = await process(input);
    expect(output).not.toContain("[ABC-1234]");
    expect(output).not.toContain("[hello-world]");
  });

  it("preserves surrounding text intact", async () => {
    const input = "Before NHP-220630FLY-1-PUB after";
    const output = await process(input);
    expect(output).toContain("Before ");
    expect(output).toContain(" after");
  });

  // SOP/protocol UID tests
  it("converts SOP UID with file extension into a /seek/sop/ link", async () => {
    const input = "See protocol P.ESS-251028-V1_NG_8-GEX.docx for details.";
    const output = await process(input);
    // remark-stringify escapes underscores in link text, so match the URL portion
    expect(output).toContain("(/seek/sop/uid=P.ESS-251028-V1_NG_8-GEX.docx/)");
  });

  it("converts SOP UID without file extension", async () => {
    const input = "Protocol P.BIO-260115-V2_extraction_protocol is ready.";
    const output = await process(input);
    expect(output).toContain("(/seek/sop/uid=P.BIO-260115-V2_extraction_protocol/)");
  });

  it("does NOT cross-contaminate SOP and sample UIDs", async () => {
    const input = "SOP P.ESS-251028-V1_test.pdf and sample NHP-220630FLY-1-PUB";
    const output = await process(input);
    expect(output).toContain("/seek/sop/uid=P.ESS-251028-V1_test.pdf/");
    expect(output).toContain("/seek/sampletree/uid=NHP-220630FLY-1-PUB/");
  });

  it("does NOT linkify SOP UIDs inside code blocks", async () => {
    const input = "```\nP.ESS-251028-V1_test.docx\n```";
    const output = await process(input);
    expect(output).not.toContain("[P.ESS-251028-V1_test.docx]");
  });
});
