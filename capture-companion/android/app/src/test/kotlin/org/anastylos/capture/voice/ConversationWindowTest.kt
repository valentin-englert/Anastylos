package org.anastylos.capture.voice

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class ConversationWindowTest {
    @Test
    fun isClosedUntilTheAgentHasSpoken() {
        assertThat(ConversationWindow().isOpen(0)).isFalse()
    }

    @Test
    fun opensFromTheEndOfSpeechAndLastsAboutFifteenSeconds() {
        val window = ConversationWindow()
        window.renew(nowMs = 10_000)
        assertThat(window.isOpen(10_001)).isTrue()
        assertThat(window.isOpen(24_999)).isTrue()
        assertThat(window.isOpen(25_000)).isFalse()
        assertThat(window.remainingMs(20_000)).isEqualTo(5_000)
        assertThat(window.remainingMs(30_000)).isEqualTo(0)
    }

    @Test
    fun everyFurtherReplyRenewsItAndCloseEndsIt() {
        val window = ConversationWindow()
        window.renew(0)
        window.renew(12_000)
        assertThat(window.isOpen(26_000)).isTrue()
        window.close()
        assertThat(window.isOpen(26_000)).isFalse()
    }
}
