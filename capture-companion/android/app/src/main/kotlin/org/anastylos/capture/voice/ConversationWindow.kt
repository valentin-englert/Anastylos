package org.anastylos.capture.voice

/**
 * The stretch after the agent has spoken in which the operator may answer without the wake word.
 * It opens when the agent's speech ends (not when it starts), lasts [durationMs] and is renewed by
 * every further spoken reply; once it has run out the wake word is needed again.
 */
class ConversationWindow(private val durationMs: Long = DURATION_MS) {
    @Volatile private var openUntilMs = 0L
    private var emptyStreak = 0

    /** Opens or renews the window from [nowMs], the end of the agent's speech. */
    fun renew(nowMs: Long) {
        openUntilMs = nowMs + durationMs
        emptyStreak = 0
    }

    /**
     * A clip heard inside the window came back empty. The operator may well have spoken, so the window
     * is not used up: it runs on from [nowMs], for at most [MAX_EMPTY_IN_A_ROW] empty clips in a row
     * (then it simply runs out, so noise cannot keep the microphone open for ever). True when extended.
     */
    fun extendAfterEmpty(nowMs: Long): Boolean {
        if (!isOpen(nowMs) || emptyStreak >= MAX_EMPTY_IN_A_ROW) return false
        emptyStreak++
        openUntilMs = nowMs + durationMs
        return true
    }

    fun close() {
        openUntilMs = 0L
        emptyStreak = 0
    }

    fun isOpen(nowMs: Long): Boolean = nowMs < openUntilMs

    fun remainingMs(nowMs: Long): Long = (openUntilMs - nowMs).coerceAtLeast(0L)

    companion object {
        const val DURATION_MS = 15_000L
        const val MAX_EMPTY_IN_A_ROW = 2
    }
}
