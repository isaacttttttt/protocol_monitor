from app.signals.models import Signal, SignalLevel


def render_signal(signal: Signal) -> tuple[str, str]:
    title = f"【{signal.symbol}｜{signal.level.value}｜{_level_title(signal.level)}】"
    if signal.level == SignalLevel.L3:
        body = (
            f"账本：{signal.book}\n"
            f"模式：{signal.strategy_name}\n"
            f"方向：{signal.direction}\n"
            f"价格：{signal.trigger_price}\n\n"
            f"触发原因：\n{signal.trigger_reason}\n\n"
            f"执行参考：\nEntry：{signal.entry}\nSL：{signal.sl}\nTP1：{signal.tp1}\nTP2：{signal.tp2}\nTP3：{signal.tp3}\n"
            f"R/R：{signal.rr_to_tp1}\n仓位：{signal.position_r}R\n\n"
            f"风控：\nBTC过滤：{signal.btc_filter}\nCVD状态：{signal.flow_state}\n风险标记：{signal.risk_flags}\n\n"
            f"失效：\n{signal.invalid_condition}\n\n信号ID：{signal.signal_id}\n时间：{signal.created_at.isoformat()}"
        )
    elif signal.level == SignalLevel.L4:
        body = (
            f"账本：{signal.book}\n模式：{signal.strategy_name}\n方向：{signal.direction}\n\n"
            f"原因：\n{signal.trigger_reason}\n\n协议处理：\n{signal.invalid_condition}\n\n"
            f"信号ID：{signal.signal_id}\n时间：{signal.created_at.isoformat()}"
        )
    else:
        body = (
            f"账本：{signal.book}\n模式：{signal.strategy_name}\n方向：{signal.direction}\n价格：{signal.trigger_price}\n\n"
            f"状态：\n{signal.trigger_reason}\n\n等待：\n继续等待触发确认条件\n\n"
            f"失效：\n{signal.invalid_condition}\n\n信号ID：{signal.signal_id}\n时间：{signal.created_at.isoformat()}"
        )
    return title, body


def _level_title(level: SignalLevel) -> str:
    return {
        SignalLevel.L1: "观察",
        SignalLevel.L2: "策略预备",
        SignalLevel.L3: "交易触发",
        SignalLevel.L4: "策略失效 / 风控",
    }[level]
