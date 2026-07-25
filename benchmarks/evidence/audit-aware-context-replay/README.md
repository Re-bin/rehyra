# Audit-aware Context replay

This directory records one real `gpt-5.6-sol` Context Agent call after the
Experience Bank feedback path was amended to include Proposal diagnostics and
trained-category audit flags.

The bank presented:

- the public 36p trained seed;
- the clean 35p gauge candidate;
- raw 34p, 31p, 28p and 24p candidates with unresolved fixed-buffer risks.

The Context Agent selected the clean 35p candidate for an exact-symmetry
search. Its second inspiration treated the 34p route only as a hypothesis to
be validated by fresh-from-scratch training, explicitly refusing to accept the
calibrated constants as category evidence.

The call consumed 20,648 input tokens and 1,203 output tokens and completed in
37.3 seconds. Isolation remains advisory.

