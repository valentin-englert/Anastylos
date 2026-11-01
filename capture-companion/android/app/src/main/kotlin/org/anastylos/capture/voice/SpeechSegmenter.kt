package org.anastylos.capture.voice

import kotlin.math.sqrt

/**
 * Local speech detection for the opt-in wake-word mode, the same rule as
 * the workstation reference (`tools/session-ui/web/app.js`, commit
 * 064df816): energy above an adaptive background floor starts a segment
 * with ~0.3 s pre-roll; ~0.8 s of quiet or 8 s of length ends it;
 * segments with less than 0.4 s of sound are dropped. Pure and
 * single-threaded: feed fixed-size 16-bit PCM blocks, receive finished
 * segments. Silence never leaves this class.
 */
class SpeechSegmenter(
    private val sampleRate: Int = SAMPLE_RATE,
    private val blockSamples: Int = BLOCK_SAMPLES,
    private val startFactor: Double = 3.0,
    private val minFloor: Double = 0.004,
    private val hangMs: Double = 800.0,
    private val maxMs: Double = 8000.0,
    private val minMs: Double = 400.0,
    private val prerollBlocks: Int = 3,
) {
    private val blockMs = blockSamples * 1000.0 / sampleRate
    private var floor = 0.01
    private val preroll = ArrayDeque<ShortArray>()
    private var segment: MutableList<ShortArray>? = null
    private var quietMs = 0.0

    /** Background level (RMS, 0..1), learned only outside speech. */
    val backgroundRms: Double get() = floor

    /** True while a segment is being collected. */
    val inSpeech: Boolean get() = segment != null

    /**
     * One block of PCM. Returns a finished segment (all samples, pre-roll
     * included) or null. Blocks of another size are rejected.
     */
    fun feed(block: ShortArray): ShortArray? {
        require(block.size == blockSamples) { "need $blockSamples samples per block" }
        val level = rms(block)
        val threshold = maxOf(minFloor, floor * startFactor)
        val open = segment
        if (open == null) {
            floor = 0.95 * floor + 0.05 * level
            preroll.addLast(block.copyOf())
            if (preroll.size > prerollBlocks) preroll.removeFirst()
            if (level > threshold) {
                segment = preroll.toMutableList()
                preroll.clear()
                quietMs = 0.0
            }
            return null
        }
        open += block.copyOf()
        quietMs = if (level > threshold) 0.0 else quietMs + blockMs
        val lengthMs = open.size * blockMs
        if (quietMs < hangMs && lengthMs < maxMs) return null
        segment = null
        val soundMs = lengthMs - quietMs
        return if (soundMs >= minMs) concat(open) else null
    }

    /** Drops any partial segment, e.g. while the phone speaks. Floor is kept. */
    fun reset() {
        segment = null
        quietMs = 0.0
        preroll.clear()
    }

    private fun concat(blocks: List<ShortArray>): ShortArray {
        val out = ShortArray(blocks.sumOf { it.size })
        var offset = 0
        for (b in blocks) {
            b.copyInto(out, offset)
            offset += b.size
        }
        return out
    }

    companion object {
        const val SAMPLE_RATE = 16_000
        /** 100 ms at 16 kHz. */
        const val BLOCK_SAMPLES = 1_600

        fun rms(block: ShortArray): Double {
            var sum = 0.0
            for (s in block) {
                val v = s / 32768.0
                sum += v * v
            }
            return sqrt(sum / block.size)
        }
    }
}
