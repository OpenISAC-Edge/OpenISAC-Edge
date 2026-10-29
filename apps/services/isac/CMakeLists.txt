add_library(ocudu_isac STATIC
        isac_publisher.cpp
        isac_zmq_sink.cpp)
target_include_directories(ocudu_isac PRIVATE ${CMAKE_SOURCE_DIR})
target_link_libraries(ocudu_isac ${ZEROMQ_LIBRARIES} ocudu_isac_tap ocudu_phy_support ocudu_support fmt)
