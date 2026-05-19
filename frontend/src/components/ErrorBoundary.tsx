import React from "react";

interface Props {
  children: React.ReactNode;
  /** 行内兜底（提供则用紧凑卡片，不整屏白屏；不提供则整屏兜底） */
  inline?: boolean;
  /** 这些 key 变化时自动复位错误状态（如切换 turn / 会话） */
  resetKeys?: ReadonlyArray<unknown>;
  /** 自定义兜底渲染 */
  fallback?: (error: Error, reset: () => void) => React.ReactNode;
}

interface State {
  error: Error | null;
}

function shallowDiff(a?: ReadonlyArray<unknown>, b?: ReadonlyArray<unknown>): boolean {
  if (a === b) return false;
  if (!a || !b || a.length !== b.length) return true;
  for (let i = 0; i < a.length; i++) if (!Object.is(a[i], b[i])) return true;
  return false;
}

export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidUpdate(prev: Props) {
    // 切换 turn/会话后自动复位，避免一次渲染错误把组件永久卡死
    if (this.state.error && shallowDiff(prev.resetKeys, this.props.resetKeys)) {
      this.setState({ error: null });
    }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("DataChat render crashed", error, info.componentStack);
  }

  reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    if (this.props.fallback) return this.props.fallback(error, this.reset);

    if (this.props.inline) {
      // 行内兜底：只坏这一张卡，整页其余功能照常可用，绝不白屏
      return (
        <div className="qq-card w-full border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-600">
          <div className="font-medium">这条结果渲染异常</div>
          <div className="mt-1 text-xs leading-6 text-rose-500">
            其它功能不受影响，可继续提问。{error.message}
          </div>
          <button className="qq-btn mt-2 px-2 py-1 text-xs" onClick={this.reset}>
            重试渲染
          </button>
        </div>
      );
    }

    return (
      <div className="flex h-full items-center justify-center bg-[#f5f7fc] px-6">
        <div className="qq-card max-w-lg px-6 py-5">
          <div className="text-base font-semibold text-rose-600">页面渲染异常</div>
          <div className="mt-2 text-xs leading-6 text-slate-500">
            当前页面组件出错，但服务没有中断。可点击下方按钮恢复。
          </div>
          <pre className="mt-3 max-h-36 overflow-auto rounded-lg bg-rose-50 px-3 py-2 text-[11px] text-rose-700">
            {error.message}
          </pre>
          <div className="mt-4 flex gap-2">
            <button className="qq-btn-primary" onClick={this.reset}>
              恢复页面
            </button>
            <button className="qq-btn" onClick={() => location.reload()}>
              刷新页面
            </button>
          </div>
        </div>
      </div>
    );
  }
}
