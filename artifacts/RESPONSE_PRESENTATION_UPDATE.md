# Response presentation and completion — 21 October 2026

## Changes

- The completion timestamp is captured once. Idle redraws no longer advance a completion clock.
- The completed task clears its thinking/streaming flags and active generation handler, records an explicit finished/stopped/cancelled progress event, and labels the expanded trace Completed. Existing reasoning remains available through Ctrl+O.
- Every main task prints a permanent footer instead of keeping only the most recent footer in the live input dock. It measures task execution, excluding time spent waiting in the pending queue. Successful replies say Worked for; errors and cancellations have distinct labels.
- New replies save compact timing and usage metadata in their conversation. Resume replays these footers. Existing history without saved measurements cannot be reconstructed accurately.
- Side replies have independent per-question accounting. Local greetings receive a zero API token footer and are saved as conversation messages.
- Tables use aligned columns and borders; small screens use labeled row fields when a table would be unreadable. Headings, lists, numbered steps, blockquotes, code blocks, and error/note text receive appropriate local layouts. Code blocks display line numbers and wrap without clipping source. Tool cards preserve indentation and show failure truthfully when output is empty.
- Explicit unordered filename lists are sorted alphabetically. General lists, numbered procedures, logs and table rows retain their original order. Inline code preserves literal filename/operator characters such as `__init__.py` and `a**b`.

See RESPONSE_LAYOUT_PREVIEW.txt for a plain-text illustration. The terminal additionally uses color, including red deletions and green additions.

## Token accounting

Input + output = total across all actual requests made for that task, including continuation and retry attempts. Provider-reported counts take precedence. Cached and reasoning detail counts are not added again when already included in provider totals. Nested usage envelopes, including Groq's x_groq and Responses/message envelopes, are recognized.

If a provider does not return complete usage, the footer explicitly marks estimates based on the sent request and observed generated content/reasoning/tool arguments. They are approximate character-based estimates, not native tokenizer measurements. Unobserved output on a failed/cancelled request remains unavailable rather than being presented as zero. Token totals do not estimate price or prove provider billing.

OpenRouter's current [official usage documentation](https://openrouter.ai/docs/cookbook/administration/usage-accounting) describes automatic usage in the final stream chunk or complete response and says legacy include-usage flags have no effect. No unsupported global request flags were added. Missing provider measurements remain honestly labeled instead of requiring extra network calls.

Formatting, activity labels and completion metadata make no AI requests. Saved footer metadata is excluded from outgoing model context. Stored assistant source is unchanged by rendering. Existing approval, tool execution, queue and endpoint selection behavior is preserved.

## Verification

- Added tests/test_response_presentation.py: 15 checks covering provider/missing/partial usage, multiple requests and replies, fixed history footers, side-chat accounting isolation, cancellation/error cleanup, context exclusion, tables, narrow layouts, long code, Unicode, filenames and diff colors.
- Full suite: **228 tests passed**. Modified Python modules also passed compileall.
- No paid or live endpoint requests were made for this update; router fixtures and real local terminal/history behavior were used.

## Recovery

Pre-change copies are at /root/galam-upgrade-backups/response-footer-20261021-031002/. They preserve the pre-existing work in this checkout. Restart Galam to load the updated Python modules.
