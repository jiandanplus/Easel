/** Presentation copy for new request errors; saved history remains untouched. */
export function requestFailureMessage(message: string): string {
  return message === 'Pi 模型或运行时执行失败，请检查供应商设置或中央执行状态'
    ? '本次请求未完成，请查看请求状态'
    : message;
}
