# Token Efficiency

Minimise token usage - this directly affects cost and speed:

- **Don't poll or re-read**: For background tasks, wait for completion once rather than repeatedly reading output files.

- **Skip redundant verification**: After a tool succeeds without error, don't re-read the result to confirm.

- **Match verbosity to task complexity**: Routine ops (merge, deploy, simple file edits) need minimal commentary. Save detailed explanations for complex logic, architectural decisions, or when asked.

- **One tool call, not three**: Prefer a single well-constructed command over multiple incremental checks.

- **Don't narrate tool use**: Skip "Let me read the file" or "Let me check the status" ? just do it.

**CRITICAL - Context preservation:** Background tasks return completion notifications with `<result>` tags containing only the final message. Do NOT call `TaskOutput` to check results. `TaskOutput` returns the full conversation transcript (every tool call, file read, and intermediate message), which wastes massive amounts of context. Wait for each task's completion notification and use the `<result>` tag content directly.