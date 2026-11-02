package org.anastylos.capture.ui.guided

import com.google.common.truth.Truth.assertThat
import org.anastylos.capture.guidance.SessionState
import org.anastylos.capture.sync.GuidedViewRequest
import org.anastylos.capture.voice.VoiceController
import org.junit.Test

class StatusBannerTest {
    @Test
    fun notAttachedAsksForASession() {
        val banner = statusBannerOf(GuidanceUiState())
        assertThat(banner.title).isEqualTo("No session yet")
        assertThat(banner.tone).isEqualTo(BannerTone.PROBLEM)
    }

    @Test
    fun attachedAndArmedWaitsForARequest() {
        val banner = statusBannerOf(GuidanceUiState(remoteAttached = true, session = SessionState.ACTIVE))
        assertThat(banner.title).isEqualTo("Waiting for a request")
    }

    @Test
    fun aRequestShowsItsLabelAsDetail() {
        val state = GuidanceUiState(
            remoteAttached = true,
            session = SessionState.ACTIVE,
            remoteRequest = GuidedViewRequest(requestId = "r1", targetLabel = "Back shell"),
        )
        assertThat(statusBannerOf(state).detail).isEqualTo("Back shell")
    }

    @Test
    fun voiceLabelPrefersSpeakingThenThinkingThenListening() {
        assertThat(voiceLabelOf(VoiceController.State())).isNull()
        assertThat(voiceLabelOf(VoiceController.State(listening = true))).isEqualTo("Listening")
        assertThat(voiceLabelOf(VoiceController.State(listening = true, busy = true))).isEqualTo("Thinking")
        assertThat(voiceLabelOf(VoiceController.State(listening = true, busy = true, speaking = true))).isEqualTo("Speaking")
    }
}
