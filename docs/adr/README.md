# Architecture decision records

An ADR records one decision that is hard to reverse, would surprise a new reader ("why on earth?"), and had real alternatives. If one of those three is missing, no ADR: a code comment, a README or the commit message is enough.

Files are numbered `0001-slug.md`, `0002-slug.md`; scan for the highest number and add one. The format is the title as a heading, then one to three sentences: the context, what was decided, and why.

```md
# <Short title of the decision>

<1-3 sentences: the context, what was decided, and why.>
```

Optional, only when they earn it: a status line (`proposed | accepted | deprecated | superseded by ADR-NNNN`) when a decision is revisited; considered options when the rejected ones are worth remembering; consequences when a downstream effect is not obvious.

There are no ADRs yet. The words an ADR uses come from [`../GLOSSARY.md`](../GLOSSARY.md).
