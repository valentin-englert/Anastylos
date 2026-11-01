package org.anastylos.capture.voice

import com.google.common.truth.Truth.assertThat
import org.anastylos.capture.voice.WakeWord.FixedCommand
import org.anastylos.capture.voice.WakeWord.Route
import org.junit.Test

class WakeWordTest {
    @Test
    fun wakeWordVariantsCarryTheCommand() {
        assertThat(WakeWord.route("Muse, next question.", armed = false)).isEqualTo(Route.Command(FixedCommand.NEXT_QUESTION))
        assertThat(WakeWord.route("hey Meta repeat", armed = false)).isEqualTo(Route.Command(FixedCommand.REPEAT))
        assertThat(WakeWord.route("OK mews, say that again", armed = false)).isEqualTo(Route.Command(FixedCommand.REPEAT))
        assertThat(WakeWord.route("Muse: screw from the back shell", armed = false))
            .isEqualTo(Route.Input("screw from the back shell"))
    }

    @Test
    fun unaddressedSpeechIsDiscarded() {
        assertThat(WakeWord.route("screw from the back shell", armed = false)).isEqualTo(Route.Discard)
        assertThat(WakeWord.route("amuse me", armed = false)).isEqualTo(Route.Discard)
        assertThat(WakeWord.route("metadata is fine", armed = false)).isEqualTo(Route.Discard)
    }

    @Test
    fun bareWakeWordArmsAndArmedAcceptsCommand() {
        assertThat(WakeWord.route("Muse.", armed = false)).isEqualTo(Route.Armed)
        assertThat(WakeWord.route("yes, that is correct", armed = true)).isEqualTo(Route.Input("yes, that is correct"))
    }
}
