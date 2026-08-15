# closing.zh-CN

场景分析任务的收尾自检提示词。按照《调用者-Core交互规范》4.2节的组装规则,本文件在每一轮调用中都会与`system`一起发送,不只在最终完成时提醒一次,目的是让自检要求在每一轮生成前都保持高权重。

---

<!-- PROMPT:BEGIN -->
Before you write or continue writing anything in this round, work through the following checklist in your head. Do not include this checklist, or any statement that you performed it, anywhere in your output. Your response must contain only the analysis document itself, in the exact format defined by the system prompt, with no preamble and no closing remarks.

1. Does every cue, from first to last, belong to exactly one unit, with no gaps and no overlaps.
2. Does every unit have its situational description, intensity, and medium tagged, along with speaker judgments covering as many cues as possible.
3. Has every locked translation that spans multiple cues already been split by cue in advance.
4. Does every locked translation and note satisfy the punctuation rules and the Simplified Chinese requirement, with no em dashes anywhere.
5. Has a full scan for recurring phrases been completed across the film, with a distinct rendering given for every individual occurrence, and has a place where the original was deliberately near-verbatim repeated preserved that echo rather than being broken apart by natural, independent translation.
6. Is every background note genuinely necessary and confirmed to be something the translation itself cannot carry, rather than plain background trivia, and does any note give away a later plot development too early.
7. Has every piece of material referenced across units been fully restated inside every unit that needs it.
8. Have any terminology or character entries already marked confirmed been altered, rather than only commented on with a suggestion.
9. Have locked translations, translation suggestions, and the decision of whether a note is needed all gone through weighing multiple approaches.
10. Do the three categories, address variants, metaphor in proper nouns, and name-to-name connections, cover every relevant case that actually appears in the film.
11. Is every rendering of offensive language faithful to the original's strength and intent, without adding or softening a targeted meaning the original did not carry.
12. Does the understanding of each character cover all four perspectives, the character's own, other characters' view of them, audience perception, and the writer's intent, rather than stopping at the character's own psychology alone.
13. Does the unit segmentation follow scene and shot continuity, without mistakenly splitting a unit over a momentary interruption inside a single scene.
14. For any line quoting a religious text, a literary work, a poem, or a song, has it been checked for an existing authoritative Chinese translation and used where one exists, with anything unconfirmed flagged in the translation suggestions as needing verification.
15. Has every cue across the film that uses a different language from the main dialogue been flagged, along with its specific language.
16. Have units of measurement, currency, and date formats been handled according to mainland Chinese convention or annotated where needed, and has any accent that carries class or regional information been given a specific suggested rendering in the translation suggestions.
17. Does the `全局格式约定` table cover number formatting, currency, how time is read aloud, percentages, default formality, abbreviation expansion, and unit conversion, has it captured the actual exceptions that appear in the film, and is every unit's content consistent with this table.

If this round is a continuation of a previous round in the same task, also confirm: have you avoided repeating any unit, table, or section that was already fully written in an earlier round, and does the heading structure, numbering, and level of detail in this round's output match what was already established, with no drift in formatting partway through the document.

Only after this check should you write or continue writing the document.
<!-- PROMPT:END -->

---

<div align="center">

**蒙太奇字幕社区 (MontageSubs)**  
"用爱发电 ❤️ Powered by Love"

</div>
