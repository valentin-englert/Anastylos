package org.anastylos.capture.camera

import androidx.camera.core.CameraSelector
import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * Stored-ID resolution and operator labels (pure): exact match
 * wins, a missing request falls back with a note, and labels
 * carry only measured facing/ID/focal facts.
 */
class CameraInventoryTest {
    private fun cam(
        id: String,
        facing: Int = CameraSelector.LENS_FACING_BACK,
        focal: Float? = 5.2f,
    ) = UsableCamera(id, cameraLabel(id, facing, focal), facing, focal, null, null, false, "LIMITED")

    @Test
    fun exactStoredMatchWinsWithoutNote() {
        val resolution = resolveCamera("2", listOf(cam("0"), cam("2"), cam("1")))!!
        assertThat(resolution.camera.id).isEqualTo("2")
        assertThat(resolution.fellBack).isFalse()
        assertThat(resolution.note).isNull()
    }

    @Test
    fun missingStoredIdFallsBackToMainBackWithNote() {
        val resolution = resolveCamera("9", listOf(cam("2"), cam("0")))!!
        assertThat(resolution.camera.id).isEqualTo("0")
        assertThat(resolution.fellBack).isTrue()
        assertThat(resolution.note).contains("9")
        assertThat(resolution.note).contains("0")
    }

    @Test
    fun freshDefaultIsLowestBackWithoutNote() {
        val front = cam("1", CameraSelector.LENS_FACING_FRONT, 3.7f)
        val resolution = resolveCamera(null, listOf(cam("2"), front, cam("0")))!!
        assertThat(resolution.camera.id).isEqualTo("0")
        assertThat(resolution.fellBack).isFalse()
        assertThat(resolution.note).isNull()
    }

    @Test
    fun frontOnlyInventoryResolvesToFront() {
        val resolution = resolveCamera(null, listOf(cam("1", CameraSelector.LENS_FACING_FRONT)))!!
        assertThat(resolution.camera.id).isEqualTo("1")
        assertThat(resolution.fellBack).isFalse()
    }

    @Test
    fun emptyInventoryResolvesNull() {
        assertThat(resolveCamera("0", emptyList())).isNull()
        assertThat(resolveCamera(null, emptyList())).isNull()
    }

    @Test
    fun labelsCarryFacingIdAndFocal() {
        assertThat(cameraLabel("0", CameraSelector.LENS_FACING_BACK, 5.23f))
            .isEqualTo("Back 0 · 5.2 mm")
        assertThat(cameraLabel("1", CameraSelector.LENS_FACING_FRONT, 3.72f))
            .isEqualTo("Front 1 · 3.7 mm")
        assertThat(cameraLabel("2", CameraSelector.LENS_FACING_BACK, null)).isEqualTo("Back 2")
        assertThat(cameraLabel("3", 99, null)).isEqualTo("Camera 3")
    }
}
