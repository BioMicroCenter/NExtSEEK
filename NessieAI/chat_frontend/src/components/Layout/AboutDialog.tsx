import { useId, type ReactNode } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/**
 * The Nessie README page: what Nessie is, the data model, how to download
 * data, and how long an answer takes.
 *
 * Mounted by BOTH shells (EmbeddedApp and AppLayout), opened from the
 * toolbar's "About Nessie" button. The embedded stylesheet has no preflight and
 * the host page's own element styles still apply here, so every element sets
 * its own margins and sizes.
 *
 * The timing copy promises a stop at about three minutes. That is the Container
 * turn ceiling (NEXTSEEK_CC_TIMEOUT_HARD_MAX, default 180 s, in
 * NessieAI/cc/cc_engine.py), kept by operator decision; the unit test reads that
 * default and fails if it moves, so the copy changes with it. A search turn has
 * no ceiling at all (it runs in a thread with no time limit and the client polls
 * until it ends), so the copy does not promise that a search ends in a minute.
 */

interface AboutDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const LIST = "m-0 list-disc space-y-1.5 pl-5 text-sm leading-relaxed";
const PARA = "m-0 text-sm leading-relaxed";

function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="space-y-2">
      <h3 id={id} className="m-0 text-base font-semibold">
        {title}
      </h3>
      {children}
    </section>
  );
}

function Field({ children }: { children: ReactNode }) {
  return <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">{children}</code>;
}

export function AboutDialog({ open, onOpenChange }: AboutDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>About Nessie</DialogTitle>
          <DialogDescription>
            What Nessie answers, how NExtSEEK data is organized, how to download it, and how
            long an answer takes.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5">
          <Section title="What Nessie is">
            <p className={PARA}>
              Nessie is the NExtSEEK assistant. Ask in plain English about the samples, data files
              and projects registered in NExtSEEK; you do not need to know any query syntax. Each
              message goes to one of two engines:
            </p>
            <ul className={LIST}>
              <li>
                <strong>Search.</strong> Turns your question into NExtSEEK searches and graph
                queries, then summarizes what comes back. Finding samples, counting them, following
                lineage and describing a sample type or assay all go here.
              </li>
              <li>
                <strong>Step-by-step tasks.</strong> An assistant that works through several steps
                in its own sandbox, for requests such as preparing a file or combining several
                lookups.
              </li>
            </ul>
            <p className={PARA}>
              Follow-ups build on your last answer: &ldquo;which of those are from 2024?&rdquo; or
              &ldquo;same search, but liver only&rdquo;. Nessie answers only from what is
              registered in NExtSEEK, so a general science question, or one about data held
              elsewhere, gets a short reply saying it is out of scope. It can build GEO, SRA and
              PRIDE submission packages from NExtSEEK records.
            </p>
          </Section>

          <Section title="The data model">
            <ul className={LIST}>
              <li>
                <strong>Sample.</strong> Every record in NExtSEEK is a sample: an animal or
                patient, a tissue, an extract, and also a data file or an analysis result. Each has
                a UID that begins with its sample type code, such as <Field>MUS-</Field> for a
                mouse or <Field>D.SEQ-</Field> for a sequencing file.
              </li>
              <li>
                <strong>Sample type.</strong> The kind of record, named by a short code: MUS
                (mouse), TIS (tissue), RNA, D.SEQ (sequencing data), A.GEX (gene expression
                analysis). Types fall into four groups: sources (animals, patients, cells, microbes,
                reagents), processed material (tissues, extracts, nucleic acids, slides), raw data
                (codes starting D.) and analyses (codes starting A.).
              </li>
              <li>
                <strong>Attribute.</strong> A metadata field that a sample type defines. Every type
                requires <Field>UID</Field> and <Field>Scientist</Field>, most name a{" "}
                <Field>Parent</Field>, and each adds its own, such as Protocol, Concentration or
                Sequencer. Ask &ldquo;what does the D.FLOW sample type contain?&rdquo; to see one.
              </li>
              <li>
                <strong>Assay.</strong> The experimental step that turns a parent sample into child
                samples. Tissue Collection takes tissue from an animal; Short Read Sequencing turns
                a DNA library into sequencing data.
              </li>
              <li>
                <strong>Project.</strong> Samples belong to projects. Within a project they are
                grouped into investigations and studies, and you can scope a question to any of
                them by name.
              </li>
              <li>
                <strong>Lineage graph.</strong> Every sample names the sample it came from, so
                records form a family tree: a mouse, a tissue taken from it, DNA extracted from the
                tissue, the sequencing data from that DNA, and the analysis of that data. The graph
                also records which assay made each step. Ask for everything derived from a sample,
                or where a data file came from.
              </li>
            </ul>
          </Section>

          <Section title="Downloading data">
            <ul className={LIST}>
              <li>
                <strong>Tables in an answer.</strong> The chat shows the first rows of a long
                table. The Download button on each table saves all of it as a spreadsheet, and when
                an answer has several tables, Download All Tables (.xlsx) puts them in one
                workbook.
              </li>
              <li>
                <strong>Files an answer made.</strong> Reports, submission workbooks, sample sheets
                and full search results appear as buttons under the answer; click one to save it. A
                step-by-step task that makes several files hands them over as one zip.
              </li>
              <li>
                <strong>Every field, with the whole lineage.</strong> A table in an answer holds
                what that answer needed. For every field of a set of samples, plus everything above
                and below them in the lineage, open Sample Search, then Graph Search, in the site
                menu, tick the rows you want and click Download samples. You get one workbook with
                a sheet per sample type and a README sheet. A sample&rsquo;s own page has the same
                download, as Download All Samples.
              </li>
              <li>
                <strong>The data files themselves.</strong> Nessie does not send raw files such as
                FASTQ, FCS or images. Each data and analysis record names its file (
                <Field>File_PrimaryData</Field>), where the file is stored (
                <Field>Link_PrimaryData</Field>) and its checksum (
                <Field>Checksum_PrimaryData</Field>). Ask for those fields, download the table,
                then fetch the files from that location and check each one against its checksum.
              </li>
              <li>
                <strong>The record behind an answer.</strong> In the Debug panel, JSON saves
                everything behind the latest answer, including the raw search result, and Metadata
                saves how that answer was produced, without the results.
              </li>
            </ul>
          </Section>

          <Section title="How long an answer takes">
            <ul className={LIST}>
              <li>
                A search, a count or a catalog question usually answers in under a minute. A search
                that gathers a very large set, such as everything derived from one animal, can take
                several minutes.
              </li>
              <li>
                A step-by-step task takes longer and can run for up to about three minutes. At
                three minutes it is stopped, and the chat shows an error saying the turn
                &ldquo;exceeded the 180s limit and was stopped&rdquo;.
              </li>
              <li>
                While Nessie works, the progress steps in the chat show what is running, and the
                message box stays locked until the answer arrives.
              </li>
              <li>
                If a task is stopped, ask for less at once: one project, one sample type or one
                file per message, then build on the answer with a follow-up.
              </li>
            </ul>
          </Section>
        </div>
      </DialogContent>
    </Dialog>
  );
}
