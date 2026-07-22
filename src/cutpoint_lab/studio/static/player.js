/* 播放引擎：成片/原片双模式 + 多段试听（接缝试听）。 */
import { el, pb, state, setStatus, fmtClock, markActive } from "./shared.js";

export function resetPlayback() {
  pb.audition = null;
  pb.ranges = [];
  pb.prefix = [];
  pb.editedTotal = 0;
  pb.rangeIndex = 0;
  if (pb.raf) { cancelAnimationFrame(pb.raf); pb.raf = null; }
}

export function setRanges(ranges) {
  pb.ranges = ranges || [];
  pb.prefix = [];
  let total = 0;
  for (const item of pb.ranges) {
    pb.prefix.push(total);
    total += Math.max(0, item.end_ms - item.start_ms);
  }
  pb.editedTotal = total;
  if (pb.rangeIndex >= pb.ranges.length) pb.rangeIndex = 0;
  updateTransport();
}

export function rangeIndexForRaw(rawMs) {
  for (let i = 0; i < pb.ranges.length; i++) {
    if (rawMs >= pb.ranges[i].start_ms - 40 && rawMs <= pb.ranges[i].end_ms + 40) return i;
  }
  let best = -1;
  let bestGap = Infinity;
  for (let i = 0; i < pb.ranges.length; i++) {
    const gap = pb.ranges[i].start_ms - rawMs;
    if (gap >= 0 && gap < bestGap) { bestGap = gap; best = i; }
  }
  return best >= 0 ? best : 0;
}

/* range 的 source_segment_ids 可能是子片段 id（句内剪切后形如 "sentence_0001#2"），
   归属判断按 "#" 前缀归一到原句 id。 */
export function segmentBaseId(rawId) {
  return String(rawId).split("#")[0];
}

export function rangeIndexForRow(row) {
  return pb.ranges.findIndex((item) => (item.source_segment_ids || []).some((id) => segmentBaseId(id) === row.id));
}

export function editedElapsed() {
  if (!pb.ranges.length) return 0;
  const current = pb.ranges[pb.rangeIndex];
  const rawMs = el.video.currentTime * 1000;
  const inRange = Math.min(Math.max(rawMs - current.start_ms, 0), current.end_ms - current.start_ms);
  return pb.prefix[pb.rangeIndex] + inRange;
}

export function seekEdited(editedMs) {
  if (!pb.ranges.length) return;
  editedMs = Math.min(Math.max(0, editedMs), Math.max(0, pb.editedTotal - 1));
  let index = pb.ranges.length - 1;
  for (let i = 0; i < pb.ranges.length; i++) {
    const length = pb.ranges[i].end_ms - pb.ranges[i].start_ms;
    if (editedMs < pb.prefix[i] + length) { index = i; break; }
  }
  pb.rangeIndex = index;
  el.video.currentTime = (pb.ranges[index].start_ms + (editedMs - pb.prefix[index])) / 1000;
}

function tick() {
  pb.raf = null;
  if (el.video.paused) { updateTransport(); return; }
  const nowMs = el.video.currentTime * 1000;
  if (pb.audition) {
    const part = pb.audition.parts[pb.audition.index];
    if (nowMs >= part.endMs - 30) {
      if (pb.audition.index + 1 < pb.audition.parts.length) {
        pb.audition.index += 1;
        el.video.currentTime = pb.audition.parts[pb.audition.index].startMs / 1000;
      } else {
        el.video.pause();
        pb.audition = null;
        setStatus("试听结束。按空格继续播放成片。");
        updateTransport();
        return;
      }
    }
  } else if (pb.mode === "edited" && pb.ranges.length) {
    const current = pb.ranges[pb.rangeIndex];
    if (nowMs < current.start_ms - 250 || nowMs > current.end_ms + 250) {
      pb.rangeIndex = rangeIndexForRaw(nowMs);
      const target = pb.ranges[pb.rangeIndex];
      if (nowMs < target.start_ms - 40 || nowMs > target.end_ms + 40) {
        el.video.currentTime = target.start_ms / 1000;
      }
    } else if (nowMs >= current.end_ms - 45) {
      if (pb.rangeIndex + 1 >= pb.ranges.length) {
        el.video.pause();
        setStatus("成片播放完毕。");
        updateTransport();
        return;
      }
      pb.rangeIndex += 1;
      el.video.currentTime = pb.ranges[pb.rangeIndex].start_ms / 1000;
    }
  }
  syncActiveRow();
  updateTransport();
  pb.raf = requestAnimationFrame(tick);
}

export function syncActiveRow() {
  const nowMs = el.video.currentTime * 1000;
  const row = state.rows.find((item) => nowMs >= item.start_ms && nowMs <= item.end_ms);
  if (row) markActive(row.id, !el.video.paused);
}

export function updateTransport() {
  const playing = !el.video.paused && !el.video.ended;
  el.playBtn.textContent = playing ? "⏸" : "▶";
  let elapsed;
  let total;
  if (pb.mode === "edited" && !pb.audition) {
    elapsed = editedElapsed();
    total = pb.editedTotal;
  } else {
    elapsed = el.video.currentTime * 1000;
    total = state.sourceDurationMs || (el.video.duration || 0) * 1000;
  }
  el.timeLabel.textContent = `${fmtClock(elapsed)} / ${fmtClock(total)}`;
  el.progressFill.style.width = total ? `${Math.min(100, (elapsed / total) * 100)}%` : "0";
}

export function togglePlay() {
  if (!el.video.paused) { el.video.pause(); return; }
  pb.audition = null;
  if (pb.mode === "edited") {
    if (!pb.ranges.length) { setStatus("没有保留片段可播放，请先勾选句子。", "warn"); return; }
    if (editedElapsed() >= pb.editedTotal - 60) {
      seekEdited(0);
    } else {
      const nowMs = el.video.currentTime * 1000;
      pb.rangeIndex = rangeIndexForRaw(nowMs);
      const target = pb.ranges[pb.rangeIndex];
      if (nowMs < target.start_ms - 40 || nowMs > target.end_ms + 40) {
        el.video.currentTime = target.start_ms / 1000;
      }
    }
  }
  el.video.play();
}

export function setMode(mode) {
  if (pb.mode === mode) return;
  pb.mode = mode;
  pb.audition = null;
  el.modeToggle.querySelectorAll(".mode-btn").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === mode);
  });
  if (mode === "edited" && pb.ranges.length) {
    pb.rangeIndex = rangeIndexForRaw(el.video.currentTime * 1000);
    const target = pb.ranges[pb.rangeIndex];
    const nowMs = el.video.currentTime * 1000;
    if (nowMs < target.start_ms - 40 || nowMs > target.end_ms + 40) {
      el.video.currentTime = target.start_ms / 1000;
    }
  }
  updateTransport();
  setStatus(mode === "edited" ? "成片模式：只播放保留内容。" : "原片模式：完整播放原始素材。");
}

/* 一次性试听：单段（已删除句）或多段拼接（接缝），播完自动暂停，不改变当前模式。 */
export function auditionParts(parts, message) {
  const valid = (parts || []).filter((part) => part.endMs > part.startMs);
  if (!valid.length) return;
  pb.audition = { parts: valid, index: 0 };
  el.video.currentTime = valid[0].startMs / 1000;
  el.video.play();
  if (message) setStatus(message);
}

export function auditionRange(startMs, endMs, message) {
  auditionParts([{ startMs, endMs }], message);
}

function progressSeek(event) {
  const rect = el.progressBar.getBoundingClientRect();
  const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
  pb.audition = null;
  if (pb.mode === "edited") {
    seekEdited(ratio * pb.editedTotal);
  } else {
    el.video.currentTime = (ratio * (state.sourceDurationMs || 0)) / 1000;
  }
  updateTransport();
}

el.playBtn.addEventListener("click", togglePlay);
el.video.addEventListener("click", togglePlay);
el.video.addEventListener("play", () => { if (!pb.raf) pb.raf = requestAnimationFrame(tick); updateTransport(); });
el.video.addEventListener("pause", () => { if (pb.raf) { cancelAnimationFrame(pb.raf); pb.raf = null; } updateTransport(); });
el.video.addEventListener("timeupdate", () => { if (el.video.paused) { syncActiveRow(); updateTransport(); } });
el.progressBar.addEventListener("pointerdown", (event) => {
  progressSeek(event);
  const move = (e) => progressSeek(e);
  const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
});
el.modeToggle.addEventListener("click", (event) => {
  const button = event.target.closest(".mode-btn");
  if (button) setMode(button.dataset.mode);
});
