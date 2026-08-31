# 多模态消息管线

阶段十将文本、角色发图和 TTS 收敛到同一个持久化分段计划。这份计划是消息顺序的唯一事件源；图片和语音适配器不得再次拆分整段回复。

<style scoped>.bv-arch{font-family:Inter,"Microsoft YaHei",sans-serif;border:1px solid #dbe4f0;border-radius:16px;padding:16px;background:#f8fafc;color:#172033}.bv-layer{border-radius:12px;padding:12px;margin:8px 0;border:1px solid}.bv-title{font-weight:700;margin-bottom:8px}.bv-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px}.bv-box{background:rgba(255,255,255,.86);border:1px solid rgba(30,41,59,.12);border-radius:9px;padding:9px;font-size:13px}.bv-box small{display:block;color:#526075;margin-top:4px;line-height:1.4}.bv-user{background:#e8f2ff;border-color:#9fc6f5}.bv-app{background:#eafaf3;border-color:#93d8bd}.bv-ai{background:#f2edff;border-color:#bca8ed}.bv-data{background:#fff6dc;border-color:#e8ca72}.bv-external{background:#fff0f2;border:1px dashed #e6a2ac}.bv-arrow{text-align:center;color:#64748b;font-weight:700;letter-spacing:2px}.bv-rule{border-left:4px solid #486fd9;background:#eef3ff;padding:9px 12px;border-radius:6px;margin-top:10px;font-size:13px}</style><div class="bv-arch"><div class="bv-layer bv-user"><div class="bv-title">用户与展示层</div><div class="bv-grid"><div class="bv-box">PC / Android 聊天页<small>按分段顺序展示对白、旁白、图片状态</small></div><div class="bv-box">用户图片<small>保存本地附件；识图失败仍继续文字对话</small></div><div class="bv-box">播放与重试<small>台词可停止/重播；失败图片可按事件重试</small></div></div></div><div class="bv-arrow">↓</div><div class="bv-layer bv-app"><div class="bv-title">消息编排层</div><div class="bv-grid"><div class="bv-box">回复分类器<small>一次拆成 dialogue / narration / image</small></div><div class="bv-box">幂等图片事件<small>每轮最多一个 event_id，状态 pending / completed / failed / cancelled</small></div><div class="bv-box">延时投递器<small>依次弹出气泡，每个对白段单独进入 TTS 队列</small></div></div><div class="bv-rule">核心不变量：数据库中的 assistant_segments_json 是显示、图片和 TTS 的唯一顺序依据。</div></div><div class="bv-arrow">↓</div><div class="bv-layer bv-ai"><div class="bv-title">AI 与本地策略层</div><div class="bv-grid"><div class="bv-box">共享场景上下文<small>本地时段、已知位置/动作/服装、最近事件；不编造天气</small></div><div class="bv-box">稳定视觉身份<small>Character Card V2 扩展保存外观、默认服装和负面约束</small></div><div class="bv-box">发图门禁<small>显式索图 + 角色动作 + AI 语义判断；预算、冷却、去重、边界</small></div><div class="bv-box">结构化视觉观察<small>概览、可见物体、OCR 置信度与不确定项；不确认身份/敏感属性</small></div><div class="bv-box">TTS 文本提取<small>只保留台词；排除旁白、括号动作、图片指令与合成标签</small></div></div></div><div class="bv-arrow">↓</div><div class="bv-layer bv-data"><div class="bv-title">本地数据层</div><div class="bv-grid"><div class="bv-box">SQLite<small>轮次文字、分段 JSON、图片事件状态、角色连续性状态</small></div><div class="bv-box">AppData 媒体<small>用户图片、生成图、角色头像和 TTS 缓存</small></div><div class="bv-box">失败隔离<small>图片/TTS 超时、失败或取消只更新自身状态，不回滚文字</small></div></div></div><div class="bv-arrow">↓</div><div class="bv-layer bv-external"><div class="bv-title">外部服务适配层</div><div class="bv-grid"><div class="bv-box">文本 AI<small>DeepSeek / GRS AI：角色回复与发图语义判断</small></div><div class="bv-box">图片 AI<small>硅基流动 / GRS AI：识图与生图；高级能力必须显式声明</small></div><div class="bv-box">TTS<small>Edge / Android 系统 / 硅基流动 / 讯飞 / IndexTTS2</small></div></div></div></div>

## 关键契约

- `multimodal.py` 是无 Qt、无网络的确定性上下文层，供界面、后台任务和服务适配器共用。
- 参考图、图生图和身份一致性默认关闭；只有模型目录和适配器同时声明时才允许使用。
- 旧版自由文本识图结果按低置信度兼容；低置信度 OCR 原文不进入角色上下文。
- 角色当前发图意图必须与“想象图片”、历史照片引用、否定发图和用户退订区分。
- 图片重试使用原事件与原提示词，不重新调用角色正文模型。

## 降级与恢复

| 失败点 | 用户可见结果 | 恢复方式 |
| --- | --- | --- |
| 图片理解 | 原图保留，角色按文字继续回复 | 下一轮重新上传 |
| 图片决策 | 已完成文字保留，不创建新图片 | 显式索图可再触发 |
| 图片生成 | 图片段转为 `failed`，文字段不变 | 在原图片事件上重试 |
| TTS 合成/播放 | 文字气泡保留，语音状态恢复 | 重播当前台词段或切换引擎 |
| 取消/关闭 | 清空队列与输入状态，图片事件记为 `cancelled` | 重新打开会话后手动重试 |
