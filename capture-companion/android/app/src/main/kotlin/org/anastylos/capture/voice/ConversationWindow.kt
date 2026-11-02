package org.anastylos.capture.voice

/**
 * The stretch after the agent has spoken in which the operator may answer without the wake word.
 * It opens when the agent's speech ends (not when it starts), lasts [durationMs] and is renewed by
 * every further spoken reply; once it has run out the wake word is needed again.
 */
class ConversationWindow(private val durationMs: Long = DURATION_MS) {
    @Volatile private var openUntilMs = 0L

    /** Opens or renews the window from [nowMs], the end of the agent's speech. */
    fun renew(nowMs: Long) {
        openUntilMs = nowMs + durationMs
    }

    fun close() {
        openUntilMs = 0L
    }

    fun isOpen(nowMs: Long): Boolean = nowMs < openUntilMs

    fun remainingMs(nowMs: Long): Long = (openUntilMs - nowMs).coerceAtLeast(0L)

    companion object {
        const val DURATION_MS = 15_000L
    }
}
