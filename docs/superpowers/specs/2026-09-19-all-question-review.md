# New capture: review every model-graded question

The newly completed capture is the default target of “本次分析” and “待家长确认”. Each model-graded question, including one judged correct, is presented individually with the source/annotated page, proposed result, and an explicit parent choice. Unconfirmed model judgments do not affect knowledge statistics. Teacher-evidence decisions retain priority.

The installed desktop configuration enables this new policy for all new captures. Programmatic configurations retain their legacy default unless they opt in, keeping historical integrations and fixtures stable.

For an explicitly selected older task, review stays within that task. A split mixed-subject task includes its child documents but no unrelated tasks. A historical all-correct capture from before this fix remains available for explicit review without globally reopening every older result. Confirmation continues to the next question in the same task and updates the task outcome, exports, and statistics.

The desktop app must be rebuilt, installed without losing user data or the custom shortcut icon, smoke-tested, and pushed to the configured GitHub repository.
