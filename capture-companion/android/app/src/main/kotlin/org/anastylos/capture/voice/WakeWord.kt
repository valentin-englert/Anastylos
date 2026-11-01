package org.anastylos.capture.voice

/**
 * Wake-word routing of one transcript, the workstation rule
 * (`app.js` `WAKE`): optional "hey"/"ok", then "muse" | "meta" | "mews",
 * then the command. A bare wake word arms the next segment for 6 s.
 * Anything not addressed to the agent is [Route.Discard]: it is neither
 * shown nor kept.
 */
object WakeWord {
    const val ARMED_MS = 6_000L

    private val pattern = Regex(
        """^\W*(?:hey\s+|ok(?:ay)?\s+)?(?:muse|meta|mews)\b[\s,.!?:-]*(.*)$""",
        setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL),
    )

    sealed interface Route {
        /** Not addressed to the agent. */
        data object Discard : Route

        /** Wake word only: answer "Yes?" and accept the next segment without it. */
        data object Armed : Route

        /** A fixed command handled on the phone path. */
        data class Command(val command: FixedCommand) : Route

        /** Operator input (anastylos-live-operator-input@1, source speech_transcript). */
        data class Input(val text: String) : Route
    }

    enum class FixedCommand { NEXT_QUESTION, REPEAT }

    /** [armed] is true while a previous bare wake word is still within [ARMED_MS]. */
    fun route(transcript: String, armed: Boolean): Route {
        val trimmed = transcript.trim()
        val match = pattern.find(trimmed)
        if (match == null && !armed) return Route.Discard
        val command = match?.groupValues?.get(1)?.trim() ?: trimmed
        if (command.isEmpty()) return Route.Armed
        return when (normalize(command)) {
            "next question", "next" -> Route.Command(FixedCommand.NEXT_QUESTION)
            "repeat", "say that again" -> Route.Command(FixedCommand.REPEAT)
            else -> Route.Input(command)
        }
    }

    private fun normalize(text: String): String =
        text.lowercase().replace(Regex("[^a-z ]"), "").replace(Regex("\\s+"), " ").trim()
}
