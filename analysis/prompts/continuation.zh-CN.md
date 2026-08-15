# continuation.zh-CN

场景分析任务的续写提示词。按照《调用者-Core交互规范》4.2节,当上一轮输出因达到该轮输出token上限而被硬截断,`analysis/main.py`判断需要续写时,本文件替换首轮组装中的语料输入部分,与`system`一起在新一轮中发送,同时把上一轮的完整输出作为assistant历史消息插入对话,使模型看到自己上一轮写到哪里。

---

<!-- PROMPT:BEGIN -->
Your output in the previous round was cut off before the document was complete, because it reached that round's output length limit. Continue writing from precisely the point where you stopped.

Do not repeat any unit, table, table row, or top-level section that you already wrote in full in the previous round. Do not summarize, restate, or re-introduce anything you have already produced. If the previous round ended in the middle of a field, a table row, or a sentence, complete that piece cleanly first, without duplicating the part that was already written, and then continue to the next piece of content in the same order the system prompt's output format defines.

Resume the document exactly as though no interruption had occurred: use the same heading levels, the same unit numbering, and the same level of detail you were using immediately before the cutoff. Every rule, every phase instruction, and every formatting requirement defined in the system prompt continues to apply without exception to this continuation, including the punctuation rules, the Simplified Chinese requirement, and the ban on em dashes.

If you have already finished every unit and every section required by the output format, and the previous round's cutoff happened to land exactly on a natural boundary, state nothing extra. Simply continue with whatever section legitimately comes next, such as `全片呼应表`, `新增术语`, or `术语修订建议`, if any of those still remain unwritten.
<!-- PROMPT:END -->

---

<div align="center">

**蒙太奇字幕社区 (MontageSubs)**  
"用爱发电 ❤️ Powered by Love"

</div>
