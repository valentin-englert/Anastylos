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
    fun recordingCommandsAreAClosedSetHandledOnThePhone() {
        assertThat(WakeWord.route("Muse, start recording.", armed = false)).isEqualTo(Route.Recording(start = true))
        assertThat(WakeWord.route("hey Muse stop recording", armed = false)).isEqualTo(Route.Recording(start = false))
        assertThat(WakeWord.route("Muse, please start recording now", armed = false))
            .isEqualTo(Route.Input("please start recording now"))
        assertThat(WakeWord.route("start recording", armed = false)).isEqualTo(Route.Discard)
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

    @Test
    fun theAgentNamesWakeWordsReplaceTheFixedList() {
        val herbert = WakeWord.Names.of(listOf("Herbert", "Hurbert", "Her bert"))
        assertThat(WakeWord.route("Herbert, next question", armed = false, names = herbert))
            .isEqualTo(Route.Command(FixedCommand.NEXT_QUESTION))
        assertThat(WakeWord.route("hey her bert start recording", armed = false, names = herbert))
            .isEqualTo(Route.Recording(start = true))
        assertThat(WakeWord.route("Hurbert: where does it go", armed = false, names = herbert))
            .isEqualTo(Route.Input("where does it go"))
        assertThat(WakeWord.route("Herbert", armed = false, names = herbert)).isEqualTo(Route.Armed)
        assertThat(WakeWord.route("Muse, next question", armed = false, names = herbert)).isEqualTo(Route.Discard)
        assertThat(WakeWord.route("Herberta is here", armed = false, names = herbert)).isEqualTo(Route.Discard)
    }

    @Test
    fun withoutNamesMuseAndMetaStay() {
        assertThat(WakeWord.Names.of(emptyList())).isSameInstanceAs(WakeWord.Names.DEFAULT)
        assertThat(WakeWord.Names.of(listOf(" ", ""))).isSameInstanceAs(WakeWord.Names.DEFAULT)
        assertThat(WakeWord.route("Meta, repeat", armed = false, names = WakeWord.Names.of(emptyList())))
            .isEqualTo(Route.Command(FixedCommand.REPEAT))
    }
}
