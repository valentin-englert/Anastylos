package org.anastylos.capture.guidance

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * Canonical board geometry pins. Values transcribe
 * calibration-config.json (source SHA pinned below); any drift fails
 * here and must be fixed by regenerating, never by hand-editing.
 */
class BoardGeometryTest {
    @Test
    fun sourceHashPinned() {
        assertThat(BoardGeometry.SOURCE_CONFIG_SHA256).isEqualTo(
            "a5f32cb54b57ed49096fa7c5d4a11be6ef2e2b932b6d3d248fcf4be59c17357e",
        )
    }

    @Test
    fun surfaceA3Markers() {
        val board = BoardGeometry.SURFACE_A3
        assertThat(board.boardId).isEqualTo("surface_a3")
        assertThat(board.dictionary).isEqualTo("DICT_5X5_100")
        assertThat(board.markers.keys).containsExactly(10, 11, 12, 13, 14, 15, 16)
        val marker10 = board.markers[10]!!
        assertThat(marker10.sizeMm).isEqualTo(42.0)
        assertThat(marker10.corners).containsExactly(
            listOf(0.0, 0.0), listOf(42.0, 0.0), listOf(42.0, 42.0), listOf(0.0, 42.0),
        ).inOrder()
        // Opposite corner marker: frame spans 396 x 273.
        val marker12 = board.markers[12]!!
        assertThat(marker12.corners[2]).containsExactly(396.0, 273.0).inOrder()
    }

    @Test
    fun surfaceA4Markers() {
        val board = BoardGeometry.SURFACE_A4
        assertThat(board.dictionary).isEqualTo("DICT_5X5_100")
        assertThat(board.markers.keys).containsExactly(0, 1, 2, 3, 4)
        assertThat(board.markers.values.map { it.sizeMm }.toSet()).containsExactly(35.0)
    }

    @Test
    fun fixtureBoards() {
        assertThat(BoardGeometry.FIXTURE_FRONT.markers.keys)
            .containsExactly(20, 21, 22, 23, 24, 25)
        assertThat(BoardGeometry.FIXTURE_BACK.markers.keys)
            .containsExactly(30, 31, 32, 33, 34, 35)
        for (board in listOf(BoardGeometry.FIXTURE_FRONT, BoardGeometry.FIXTURE_BACK)) {
            assertThat(board.dictionary).isEqualTo("DICT_5X5_100")
            assertThat(board.markers.values.map { it.sizeMm }.toSet()).containsExactly(21.0)
        }
    }

    @Test
    fun charucoSpec() {
        val charuco = BoardGeometry.CHARUCO_A4
        assertThat(charuco.squaresX).isEqualTo(5)
        assertThat(charuco.squaresY).isEqualTo(7)
        assertThat(charuco.squareLengthMm).isEqualTo(30.0)
        assertThat(charuco.markerLengthMm).isEqualTo(24.0)
        assertThat(charuco.dictionary).isEqualTo("DICT_6X6_250")
        assertThat(charuco.markerIds).hasSize(17)
        assertThat(charuco.legacyPattern).isFalse()
    }

    @Test
    fun lookupByBoardId() {
        assertThat(BoardGeometry.forBoardId("surface_a3")).isSameInstanceAs(BoardGeometry.SURFACE_A3)
        assertThat(BoardGeometry.forBoardId("fixture_back")).isSameInstanceAs(BoardGeometry.FIXTURE_BACK)
        assertThat(BoardGeometry.forBoardId("charuco_a4")).isNull()
        assertThat(BoardGeometry.forBoardId("nope")).isNull()
    }

    @Test
    fun cornersCountAndOrder() {
        for (board in listOf(
            BoardGeometry.SURFACE_A3, BoardGeometry.SURFACE_A4,
            BoardGeometry.FIXTURE_FRONT, BoardGeometry.FIXTURE_BACK,
        )) {
            for ((id, marker) in board.markers) {
                assertThat(marker.corners.size).isEqualTo(4)
                // TL corner is the minimum-x, minimum-y corner (x right, y down).
                val tl = marker.corners[0]
                for (corner in marker.corners) {
                    assertThat(tl[0]).isAtMost(corner[0])
                    assertThat(tl[1]).isAtMost(corner[1])
                }
                // Square with the declared side length.
                val tr = marker.corners[1]
                val bl = marker.corners[3]
                assertThat(tr[0] - tl[0]).isWithin(1e-9).of(marker.sizeMm)
                assertThat(bl[1] - tl[1]).isWithin(1e-9).of(marker.sizeMm)
                assertThat(id).isEqualTo(marker.id)
            }
        }
    }
}
