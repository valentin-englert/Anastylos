package org.anastylos.capture.voice

/**
 * Wake-word routing of one transcript, the workstation rule
 * (`app.js` `WAKE`): optional "hey"/"ok", then the agent's wake word ([Names]),
 * then the command. A bare wake word arms the next segment for 6 s.
 * Anything not addressed to the agent is [Route.Discard]: it is neither
 * shown nor kept.
 */
object WakeWord {
    const val ARMED_MS = 6_000L

    /**
     * The spellings that count as addressing the agent. "Muse", "Meta" and "Mews" apply until the
     * operator names the agent; then the workstation's wake words and likely mishearings apply instead.
     */
    class Names private constructor(words: List<String>) {
        internal val pattern: Regex = Regex(
            """^\W*(?:hey\s+|ok(?:ay)?\s+)?(?:${words.joinToString("|") { spelling(it) }})\b[\s,.!?:-]*(.*)$""",
            setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL),
        )

        companion object {
            val DEFAULT = Names(listOf("muse", "meta", "mews"))

            /** Blank or empty falls back to [DEFAULT]. */
            fun of(words: List<String>): Names {
                val clean = words.map { it.trim() }.filter { it.isNotEmpty() }.distinct()
                return if (clean.isEmpty()) DEFAULT else Names(clean)
            }

            private fun spelling(word: String) =
                word.trim().split(Regex("""\s+""")).joinToString("""\s+""") { Regex.escape(it) }
        }
    }

    sealed interface Route {
        /** Not addressed to the agent. */
        data object Discard : Route

        /** Wake word only: answer "Yes?" and accept the next segment without it. */
        data object Armed : Route

        /** A fixed command handled on the phone path. */
        data class Command(val command: FixedCommand) : Route

        /** "start recording" / "stop recording": handled on the phone, never sent to the workstation. */
        data class Recording(val start: Boolean) : Route

        /** Operator input (anastylos-live-operator-input@1, source speech_transcript). */
        data class Input(val text: String) : Route
    }

    enum class FixedCommand { NEXT_QUESTION, REPEAT }

    /** [armed] is true while a previous bare wake word is still within [ARMED_MS]. */
    fun route(transcript: String, armed: Boolean, names: Names = Names.DEFAULT): Route {
        val trimmed = transcript.trim()
        val match = names.pattern.find(trimmed)
        if (match == null && !armed) return Route.Discard
        val command = match?.groupValues?.get(1)?.trim() ?: trimmed
        if (command.isEmpty()) return Route.Armed
        return when (normalize(command)) {
            "next question", "next" -> Route.Command(FixedCommand.NEXT_QUESTION)
            "repeat", "say that again" -> Route.Command(FixedCommand.REPEAT)
            "start recording" -> Route.Recording(start = true)
            "stop recording" -> Route.Recording(start = false)
            else -> Route.Input(command)
        }
    }

    private fun normalize(text: String): String =
        text.lowercase().replace(Regex("[^a-z ]"), "").replace(Regex("\\s+"), " ").trim()
}
