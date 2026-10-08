// Only received evidence-backed text is displayed. Completion is determined by
// the final result event, never by an idle interval or a progress percentage.
export function liveAnswerState(text: string, busy: boolean, hasResult: boolean) {
  return {
    visible: Boolean(text) && (busy || !hasResult),
    title: busy ? "已查到结果，正在补充解读" : "已接收的部分回答",
    note: busy
      ? "正文随已核验结论实时追加；关联信息仍在处理，请稍候。"
      : "本次输出尚未完整完成，以下仅保留已接收的部分正文。重新查询可获取完整结果。",
  };
}
