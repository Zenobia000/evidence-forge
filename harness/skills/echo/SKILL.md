---
name: echo
description: Read the source article and write a short summary as summary.json. Used only to prove the pipeline works end to end.
---

# echo

1. Call `read_file` with path `input/content.md` to read the source article.
2. Write two or three sentences, in the article's language, saying what the article is about.
3. Call `write_file` exactly once to create `summary.json` in the output directory named in the task. The content must be a JSON object with one field:

   ```json
   {"summary": "<your two or three sentences>"}
   ```

4. Reply with one short line saying the file was written. Do not call any other tool.
