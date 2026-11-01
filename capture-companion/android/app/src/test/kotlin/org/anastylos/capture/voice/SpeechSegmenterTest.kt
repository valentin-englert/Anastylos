package org.anastylos.capture.voice

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class SpeechSegmenterTest {
    private fun block(amplitude: Int) = ShortArray(SpeechSegmenter.BLOCK_SAMPLES) { i ->
        (if (i % 2 == 0) amplitude else -amplitude).toShort()
    }

    private fun run(seg: SpeechSegmenter, amplitude: Int, blocks: Int): List<ShortArray> =
        (0 until blocks).mapNotNull { seg.feed(block(amplitude)) }

    @Test
    fun silenceNeverProducesASegment() {
        val seg = SpeechSegmenter()
        assertThat(run(seg, 30, 600)).isEmpty()
    }

    @Test
    fun speechEndsAfterHangWithPreroll() {
        val seg = SpeechSegmenter()
        run(seg, 30, 50)
        assertThat(run(seg, 6000, 10)).isEmpty()
        val out = run(seg, 30, 8)
        assertThat(out).hasSize(1)
        // Pre-roll is 3 blocks including the triggering one (reference
        // behaviour): 2 quiet + 10 speech + 8 quiet blocks of 100 ms.
        assertThat(out[0].size).isEqualTo(20 * SpeechSegmenter.BLOCK_SAMPLES)
    }

    @Test
    fun shortBurstIsDropped() {
        val seg = SpeechSegmenter()
        run(seg, 30, 50)
        // One 100 ms click: 300 ms incl. pre-roll < 400 ms minimum.
        run(seg, 6000, 1)
        assertThat(run(seg, 30, 10)).isEmpty()
    }

    @Test
    fun longSpeechIsCutAtEightSeconds() {
        val seg = SpeechSegmenter()
        run(seg, 30, 50)
        val out = run(seg, 6000, 100)
        assertThat(out).isNotEmpty()
        assertThat(out[0].size).isAtMost(80 * SpeechSegmenter.BLOCK_SAMPLES + 3 * SpeechSegmenter.BLOCK_SAMPLES)
    }
}
