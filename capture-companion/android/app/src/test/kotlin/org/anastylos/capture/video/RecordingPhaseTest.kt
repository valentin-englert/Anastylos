package org.anastylos.capture.video

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class RecordingPhaseTest {
    @Test
    fun theEmptySheetCountsDownThenTheObjectIsPlacedThenItIsPlainRecording() {
        assertThat(recordingPhase(0)).isEqualTo(RecordingPhase.EmptySheet(5))
        assertThat(recordingPhase(1_000)).isEqualTo(RecordingPhase.EmptySheet(4))
        assertThat(recordingPhase(4_999)).isEqualTo(RecordingPhase.EmptySheet(1))
        assertThat(recordingPhase(5_000)).isEqualTo(RecordingPhase.PlaceObject)
        assertThat(recordingPhase(8_999)).isEqualTo(RecordingPhase.PlaceObject)
        assertThat(recordingPhase(9_000)).isEqualTo(RecordingPhase.Normal)
    }
}
