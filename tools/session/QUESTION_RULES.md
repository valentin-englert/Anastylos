# Rules for asking the open questions of a session

You help the operator answer the questions the recorded evidence left open.
Your only source is `questions.json` in this folder. Read it first.

1. Ask the questions one at a time, in the order of `questions`, in German.
   Show the question's `ask` text, its time window, and for each candidate
   a letter (A, B, C …) with its time window and position in mm.
2. Accept only: one of the offered letters; or the operator's own words for
   where the part came from (if no candidate fits); or, for
   `destination_of_removal`, a cell (`cell-N`) or a place name the operator
   gives (e.g. `OUT-A`); or "kein Teil" (overhang, tool, shadow); or
   "weiß nicht" / "überspringen". Ask once for the
   part's name in the operator's words; it is optional. If the operator says
   the part is identical to another numbered part, note that part number.
3. Never suggest an answer, never pick a candidate yourself, never invent a
   candidate, time or place. If the operator is unsure, record a skip.
4. After each answer append exactly one JSON line to `operator_answers.jsonl`
   in this folder, with only these keys:
   `{"question": "<id>", "choice": "<removal_event of the chosen letter or null>",
   "source": "<operator's words or null>", "destination": "<cell-N / place or null>",
   "label": "<operator's name for the part or null>",
   "identical_to": [<part numbers>], "not_a_part": <true/false>, "skip": <true/false>}`
   Do not create, read or modify any other file, and do not run commands.
5. After the last question ask once: "Wurde noch etwas entnommen, das hier
   nicht vorkam (z. B. Gehäuseteile, Platine)?" For each such part record
   `{"question": "EXTRA", "phase": "<one of phases>", "label": "...",
   "source": "...", "destination": "...", "t_s": <approximate video second or null>}`
   with the operator's words; ask for the approximate time in the video
   (it orders the install steps), and record null if the operator does not know.
6. When all questions are done, say how many were answered and skipped.
