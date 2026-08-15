# glossary_regen.zh-CN

场景分析任务的术语表终审提示词。旧版本的设计是"分析全部完成后重新生成一份完整术语表",但当前架构下`## 新增术语`与`## 术语修订建议`已经是`system`要求逐单元持续产出的正式章节,不需要另起一次从零生成。本文件的职责改为:在全片所有单元分析完成后追加一轮调用,让模型带着此刻已经建立的全片理解,回头复核自己在较早单元里做出的术语判断,处理"早期单元还没读到后续剧情、判断可能不成立"这类只有事后才能发现的问题,而不是重新发明整张表。

---

<!-- PROMPT:BEGIN -->
The full unit-by-unit analysis for this film is now complete. Across all the units you already produced, entries were added to `## 新增术语` and suggestions were added to `## 术语修订建议` as you went, each based only on the understanding you had built up to that point in the film.

Now that you have seen the entire film, revisit every entry currently sitting in `## 新增术语` and `## 术语修订建议`, in light of everything you now understand about the full story. Look specifically for:

- A term whose translation choice made in an earlier unit turns out to conflict with something the story reveals later, such as a name whose deeper meaning, covered under the system prompt's Phase 2, Item 5, only becomes clear after a plot point you had not yet reached when you first rendered it.
- Two entries that describe the same underlying term inconsistently because they were added at different points during the analysis.
- An entry you flagged only as a suggestion that, with full-film hindsight, should instead be treated as settled and folded into `## 新增术语` directly.
- Any term that should have been logged as an exception in `## 全局格式约定` but was instead handled as an ordinary terminology entry, or the reverse.

For any entry belonging to a term already marked confirmed, the confirmation-status rule in the system prompt's Section 3.3 still applies without exception: you may only add a suggested revision alongside it, you may never rewrite a confirmed entry directly, even at this final stage.

For any entry that is still a draft, correct or consolidate it directly if your full-film understanding shows it needs a change.

Output only the final, consolidated versions of `## 新增术语` and `## 术语修订建议` as your response to this round. Do not repeat any other section of the document, and do not explain what changed or why. If nothing needs to change from what you already produced, output both sections exactly as they already stand.
<!-- PROMPT:END -->

---

<div align="center">

**蒙太奇字幕社区 (MontageSubs)**  
"用爱发电 ❤️ Powered by Love"

</div>
