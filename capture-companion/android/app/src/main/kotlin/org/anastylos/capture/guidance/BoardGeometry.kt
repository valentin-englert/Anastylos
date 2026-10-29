package org.anastylos.capture.guidance

/**
 * Canonical ANASTYLOS board geometry, GENERATED — do not hand-edit.
 *
 * Source: calibration/calibration-config.json
 * Source SHA-256: a5f32cb54b57ed49096fa7c5d4a11be6ef2e2b932b6d3d248fcf4be59c17357e
 * Generator: wave2-android-toolchain/gen_board_geometry.py
 *
 * Conventions (frozen): board frame x right, y down, z into the plane;
 * marker corners ordered top_left, top_right, bottom_right, bottom_left
 * (matches OpenCV ArUco corner order); object points take z = 0.
 */

object BoardGeometry {
    const val SOURCE_CONFIG_SHA256 = "a5f32cb54b57ed49096fa7c5d4a11be6ef2e2b932b6d3d248fcf4be59c17357e"

    data class MarkerCorners(
        val id: Int,
        val sizeMm: Double,
        /** Four (x, y) corners: TL, TR, BR, BL. */
        val corners: List<List<Double>>,
    )

    data class Board(
        val boardId: String,
        val dictionary: String,
        val markers: Map<Int, MarkerCorners>,
    )

    val SURFACE_A3 = Board(
        boardId = "surface_a3",
        dictionary = "DICT_5X5_100",
        markers = mapOf(
            10 to MarkerCorners(
                id = 10,
                sizeMm = 42.0,
                corners = listOf(listOf(0.0, 0.0), listOf(42.0, 0.0), listOf(42.0, 42.0), listOf(0.0, 42.0)),
            ),
            11 to MarkerCorners(
                id = 11,
                sizeMm = 42.0,
                corners = listOf(listOf(354.0, 0.0), listOf(396.0, 0.0), listOf(396.0, 42.0), listOf(354.0, 42.0)),
            ),
            12 to MarkerCorners(
                id = 12,
                sizeMm = 42.0,
                corners = listOf(listOf(354.0, 231.0), listOf(396.0, 231.0), listOf(396.0, 273.0), listOf(354.0, 273.0)),
            ),
            13 to MarkerCorners(
                id = 13,
                sizeMm = 42.0,
                corners = listOf(listOf(0.0, 231.0), listOf(42.0, 231.0), listOf(42.0, 273.0), listOf(0.0, 273.0)),
            ),
            14 to MarkerCorners(
                id = 14,
                sizeMm = 42.0,
                corners = listOf(listOf(177.0, 0.0), listOf(219.0, 0.0), listOf(219.0, 42.0), listOf(177.0, 42.0)),
            ),
            15 to MarkerCorners(
                id = 15,
                sizeMm = 42.0,
                corners = listOf(listOf(177.0, 231.0), listOf(219.0, 231.0), listOf(219.0, 273.0), listOf(177.0, 273.0)),
            ),
            16 to MarkerCorners(
                id = 16,
                sizeMm = 42.0,
                corners = listOf(listOf(0.0, 115.5), listOf(42.0, 115.5), listOf(42.0, 157.5), listOf(0.0, 157.5)),
            ),
        ),
    )

    val SURFACE_A4 = Board(
        boardId = "surface_a4",
        dictionary = "DICT_5X5_100",
        markers = mapOf(
            0 to MarkerCorners(
                id = 0,
                sizeMm = 35.0,
                corners = listOf(listOf(0.0, 0.0), listOf(35.0, 0.0), listOf(35.0, 35.0), listOf(0.0, 35.0)),
            ),
            1 to MarkerCorners(
                id = 1,
                sizeMm = 35.0,
                corners = listOf(listOf(238.0, 0.0), listOf(273.0, 0.0), listOf(273.0, 35.0), listOf(238.0, 35.0)),
            ),
            2 to MarkerCorners(
                id = 2,
                sizeMm = 35.0,
                corners = listOf(listOf(238.0, 151.0), listOf(273.0, 151.0), listOf(273.0, 186.0), listOf(238.0, 186.0)),
            ),
            3 to MarkerCorners(
                id = 3,
                sizeMm = 35.0,
                corners = listOf(listOf(0.0, 151.0), listOf(35.0, 151.0), listOf(35.0, 186.0), listOf(0.0, 186.0)),
            ),
            4 to MarkerCorners(
                id = 4,
                sizeMm = 35.0,
                corners = listOf(listOf(0.0, 75.5), listOf(35.0, 75.5), listOf(35.0, 110.5), listOf(0.0, 110.5)),
            ),
        ),
    )

    val FIXTURE_FRONT = Board(
        boardId = "fixture_front",
        dictionary = "DICT_5X5_100",
        markers = mapOf(
            20 to MarkerCorners(
                id = 20,
                sizeMm = 21.0,
                corners = listOf(listOf(0.0, 0.0), listOf(21.0, 0.0), listOf(21.0, 21.0), listOf(0.0, 21.0)),
            ),
            21 to MarkerCorners(
                id = 21,
                sizeMm = 21.0,
                corners = listOf(listOf(201.0, 0.0), listOf(222.0, 0.0), listOf(222.0, 21.0), listOf(201.0, 21.0)),
            ),
            22 to MarkerCorners(
                id = 22,
                sizeMm = 21.0,
                corners = listOf(listOf(201.0, 121.0), listOf(222.0, 121.0), listOf(222.0, 142.0), listOf(201.0, 142.0)),
            ),
            23 to MarkerCorners(
                id = 23,
                sizeMm = 21.0,
                corners = listOf(listOf(0.0, 121.0), listOf(21.0, 121.0), listOf(21.0, 142.0), listOf(0.0, 142.0)),
            ),
            24 to MarkerCorners(
                id = 24,
                sizeMm = 21.0,
                corners = listOf(listOf(100.5, 0.0), listOf(121.5, 0.0), listOf(121.5, 21.0), listOf(100.5, 21.0)),
            ),
            25 to MarkerCorners(
                id = 25,
                sizeMm = 21.0,
                corners = listOf(listOf(100.5, 121.0), listOf(121.5, 121.0), listOf(121.5, 142.0), listOf(100.5, 142.0)),
            ),
        ),
    )

    val FIXTURE_BACK = Board(
        boardId = "fixture_back",
        dictionary = "DICT_5X5_100",
        markers = mapOf(
            30 to MarkerCorners(
                id = 30,
                sizeMm = 21.0,
                corners = listOf(listOf(0.0, 0.0), listOf(21.0, 0.0), listOf(21.0, 21.0), listOf(0.0, 21.0)),
            ),
            31 to MarkerCorners(
                id = 31,
                sizeMm = 21.0,
                corners = listOf(listOf(201.0, 0.0), listOf(222.0, 0.0), listOf(222.0, 21.0), listOf(201.0, 21.0)),
            ),
            32 to MarkerCorners(
                id = 32,
                sizeMm = 21.0,
                corners = listOf(listOf(201.0, 121.0), listOf(222.0, 121.0), listOf(222.0, 142.0), listOf(201.0, 142.0)),
            ),
            33 to MarkerCorners(
                id = 33,
                sizeMm = 21.0,
                corners = listOf(listOf(0.0, 121.0), listOf(21.0, 121.0), listOf(21.0, 142.0), listOf(0.0, 142.0)),
            ),
            34 to MarkerCorners(
                id = 34,
                sizeMm = 21.0,
                corners = listOf(listOf(100.5, 0.0), listOf(121.5, 0.0), listOf(121.5, 21.0), listOf(100.5, 21.0)),
            ),
            35 to MarkerCorners(
                id = 35,
                sizeMm = 21.0,
                corners = listOf(listOf(100.5, 121.0), listOf(121.5, 121.0), listOf(121.5, 142.0), listOf(100.5, 142.0)),
            ),
        ),
    )

    data class CharucoSpec(
        val squaresX: Int,
        val squaresY: Int,
        val squareLengthMm: Double,
        val markerLengthMm: Double,
        val dictionary: String,
        val markerIds: List<Int>,
        val legacyPattern: Boolean,
    )

    val CHARUCO_A4 = CharucoSpec(
        squaresX = 5,
        squaresY = 7,
        squareLengthMm = 30.0,
        markerLengthMm = 24.0,
        dictionary = "DICT_6X6_250",
        markerIds = listOf(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16),
        legacyPattern = false,
    )

    fun forBoardId(boardId: String): Board? = when (boardId) {
        "surface_a3" -> SURFACE_A3
        "surface_a4" -> SURFACE_A4
        "fixture_front" -> FIXTURE_FRONT
        "fixture_back" -> FIXTURE_BACK
        else -> null
    }
}
