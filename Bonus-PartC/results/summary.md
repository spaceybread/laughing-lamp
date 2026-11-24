# Part C summary

35 runs, 49.6 min in total.

| scenario | runs | duration_s | uplink_mbps | downlink_mbps | ws_rtt_med | ws_rtt_p95 | rtc_rtt_med | lat_glasses_relay_med | lat_glasses_laptop_med | fps_viewer | stalls | stall_pct | jitter_viewer_ms | rtc_jitter_ms | rtc_loss_down | failures | analyses | detect_rate | readable_rate | hint_rate | cap2hint_med | encode_ms | last_ok_age_med | tracker_lost | raw_exact_rate | pgn_raw_correct | pgn_tracked_correct | replay_correct | replay_raw_correct | replay_tracked_correct | replay_move_hit | move2pos_med |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5g-eduroam-tunnel | 10 | 850.5 | 0.799 | 0.155 | 135.0 | 304.0 | 80.2 |  |  | 0.000 | 0 | 0.000 |  | 55.8 | 0.000 | 1 | 248 | 0.981 | 0.748 | 0.507 | 2308.8 | 1033.8 | 2.524 | 51 | 0.000 |  |  |  |  |  |  |  |
| 5g-glasses-home-laptop | 10 | 851.8 | 0.671 | 0.223 | 79.8 | 195.9 | 46.8 |  |  | 19.7 | 4 | 0.000 | 31.8 | 41.6 | 0.000 | 0 | 268 | 1.000 | 1.000 | 0.491 | 1286.5 | 590.5 | 1.792 | 18 | 0.705 |  |  | 1.000 | 0.706 | 1.000 | 0.889 | 4481.0 |
| home-home | 15 | 1275.8 | 0.635 | 0.188 | 36.0 | 141.1 | 23.0 |  |  | 17.9 | 3 | 0.000 | 37.2 | 47.7 | 0.000 | 0 | 418 | 1.000 | 1.000 | 0.500 | 1315.0 | 697.5 | 1.616 | 9 | 0.704 |  |  | 1.000 | 0.773 | 1.000 | 1.000 | 4298.8 |

Columns: Mb/s for throughput, ms for RTT/latency, rates are fractions of analyses; `cap2hint` = picture taken on the
glasses -> hint drawn on the glasses; `last_ok_age` = seconds since the last readable position; `raw_exact_rate` =
share of readable frames where the raw reading already equalled the accepted position (tracker not needed).
