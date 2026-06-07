TRYON_REFINE_PROMPT = """
你是一名专业美甲试戴优化师。

当前图片已经完成甲片贴图。

你的任务：

仅优化指甲区域。

不要重绘整张图片。

目标：

让甲片呈现真实美甲效果。

优化内容：

1. 指甲边缘融合
2. 高光反射
3. 阴影过渡
4. 光照一致性
5. 材质真实感
6. 指甲弧度表现

保留：

- 原始手部结构
- 原始手指数量
- 原始肤色
- 原始背景
- 原始拍摄角度
- 原始手势
- 原始甲片设计

不要改变：

- 手指长度
- 手掌大小
- 指甲颜色
- 指甲图案
- 背景
- 光源方向

最终效果：

像真实美甲师完成后的实拍照片。

仅增强真实感。
"""

TRYON_REFINE_NEGATIVE_PROMPT = """
extra fingers,
missing fingers,
duplicate fingers,
extra nails,
wrong nail position,
deformed fingers,
deformed hands,
bad anatomy,
mutated fingers,
cropped fingers,
broken nails,
plastic nails,
sticker effect,
fake nails,
low quality,
blur,
oversaturated,
distorted hand,
cartoon hand,
extra jewelry,
changed background,
changed skin color,
new accessories,
different hand pose,
nail polish on skin,
nail design covering finger skin
"""

MULTI_IMAGE_TRYON_REFINE_PROMPT = """
第一张图片是用户真实手图。
第二张图片是目标美甲款式图。
第三张图片是已经完成贴图的试戴结果。

请基于第三张图片进行优化。

要求：

保持第一张图片中的手部结构、肤色、背景和拍摄角度。

参考第二张图片中的：
- 颜色
- 纹理
- 光泽
- 细节

保留第三张图片中的甲片位置和整体设计。

只优化：
- 甲片边缘融合
- 光照一致性
- 指甲表面高光
- 指甲弧度
- 材质真实感

禁止：
- 改变手型
- 改变手指数量
- 改变背景
- 改变肤色
- 增加饰品
- 改变美甲款式
- 让甲片覆盖到手指皮肤

目标：

生成真实自然的美甲试戴图。
"""

NAIL_STYLE_TRANSFER_PROMPT = """
你是一名专业美甲试戴生成师。

现在有两张图片：

第一张图片是用户真实手图。
第二张图片是目标美甲款式图。

你的任务是：

先从第二张美甲款式图中提取真实美甲设计特征，
再将该美甲设计自然生成到第一张用户手图的指甲区域上。

请严格遵守：

一、必须提取目标款式图中的美甲特征：
- 主色
- 辅色
- 纹理
- 图案
- 法式边
- 渐变
- 猫眼光泽
- 闪粉
- 珍珠
- 钻饰
- 甲型
- 甲长
- 光泽质感

二、必须根据用户原始指甲进行自适应改款：

如果目标款式是长甲，而用户手图是短甲：
- 保留目标款式的核心颜色、纹理和设计语言
- 将图案压缩到短甲比例
- 生成短甲适配版
- 不要强行延长用户指甲
- 不要改变用户手指长度

如果目标款式是短甲，而用户手图是长甲：
- 保留目标款式的核心颜色、纹理和设计语言
- 将图案自然延展到长甲长度
- 生成长甲适配版
- 不要让甲面显得空洞

三、必须只修改指甲区域：
- 不改变手型
- 不改变手指数量
- 不改变手指长度
- 不改变肤色
- 不改变背景
- 不改变手势
- 不改变拍摄角度
- 不新增饰品

四、生成效果要求：
- 美甲像真实做在用户指甲上
- 边缘自然贴合甲床
- 光泽和原图光照一致
- 指甲表面有真实弧度
- 不要有贴纸感
- 不要让美甲覆盖到手指皮肤

最终输出：
一张自然真实的用户美甲试戴图。
"""

NAIL_STYLE_TRANSFER_NEGATIVE_PROMPT = """
extra fingers,
missing fingers,
duplicate fingers,
extra nails,
wrong nail position,
deformed fingers,
deformed hands,
bad anatomy,
mutated fingers,
cropped fingers,
broken nails,
plastic nails,
sticker nails,
fake sticker effect,
nail polish on skin,
nail design covering finger skin,
overextended nails,
unrealistic long nails,
changed hand shape,
changed finger length,
changed skin color,
changed background,
changed pose,
new accessories,
distorted hand,
blur,
low quality,
oversaturated,
cartoon hand
"""

NAIL_STYLE_TRANSFER_WITH_DRAFT_PROMPT = """
现在有三张图片：

第一张图片是用户真实手图。
第二张图片是目标美甲款式图。
第三张图片是初步美甲贴图结果。

你的任务是：

以第一张图片的手部结构为绝对基准，
以第二张图片的美甲款式为设计参考，
以第三张图片的甲片位置为初步参考，
生成一张自然真实的美甲试戴图。

请先理解第二张目标款式图中的美甲设计：
- 主色
- 辅色
- 法式边
- 猫眼光泽
- 渐变方向
- 纹理
- 闪粉
- 珍珠
- 钻饰
- 甲型
- 甲长
- 整体风格

然后根据第一张用户手图中的真实指甲长度和甲床形状进行适配：

如果目标款式是长甲，但用户是短甲：
- 保留核心设计语言
- 生成短甲适配版
- 缩小图案比例
- 不强行延长指甲
- 不改变手指长度

如果目标款式是短甲，但用户是长甲：
- 保留核心设计语言
- 自然延展图案和光泽
- 生成长甲适配版
- 不让甲面留白突兀

关于第三张初步贴图结果：
- 可以参考其甲片位置
- 但需要修正不自然的边缘
- 修正贴纸感
- 修正光照不一致
- 修正甲片覆盖到皮肤的问题

绝对禁止：
- 改变手型
- 改变手指数量
- 改变手指长度
- 改变肤色
- 改变背景
- 改变手势
- 改变拍摄角度
- 新增饰品
- 让美甲覆盖到手指皮肤
- 生成畸形手指

最终效果：
像真实美甲师根据目标款式为用户做出的适配款，
而不是简单把图片贴上去。
"""

NAIL_STYLE_TRANSFER_WITH_EDGE_REFINE_PROMPT = """
你是一名专业美甲试戴自然化优化师。

当前图片已经完成美甲款式迁移，并经过基础边缘融合处理。

你的任务是只对指甲区域进行进一步自然化优化。

重点优化：
1. 指甲边缘与皮肤接触区域
2. 指甲根部与甲小皮过渡
3. 指甲两侧边缘阴影
4. 指甲表面镜面反射
5. 指甲横向弧度
6. 指甲纵向弧度
7. 光照与原图环境的一致性

必须保留：
- 原始手部结构
- 原始手指数量
- 原始手指长度
- 原始肤色
- 原始背景
- 原始拍摄角度
- 当前美甲颜色
- 当前美甲图案
- 当前美甲位置

严格禁止：
- 改变手型
- 改变手指数量
- 改变手势
- 改变肤色
- 改变背景
- 新增饰品
- 让美甲覆盖到皮肤
- 生成额外指甲
- 改变美甲款式设计

目标效果：
边缘自然、无贴纸感、无锯齿感，像真实美甲师完成后的实拍照片。
"""

NAIL_EDGE_NEGATIVE_PROMPT = """
hard edge,
jagged edge,
sticker effect,
plastic nail,
fake nail overlay,
nail polish on skin,
nail design covering finger skin,
extra fingers,
missing fingers,
duplicate fingers,
extra nails,
wrong nail position,
deformed fingers,
deformed hands,
bad anatomy,
mutated fingers,
changed hand shape,
changed finger length,
changed skin color,
changed background,
changed pose,
new accessories,
blur,
low quality,
oversaturated,
distorted hand
"""
