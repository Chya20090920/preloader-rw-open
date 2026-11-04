/* kernel_api_t.h — mtkpr 内核暴露给原生载荷（.dll/.pyd/.so）的唯一 ABI。
 *
 * 载荷导出：  int route_main(kernel_api_t *api);
 * 内核加载器：preloader_rw/core/kernel/loader.py（ctypes.CDLL）。
 *
 * 约定：
 *   · C ABI 只传原始帧，XML 字段由载荷自行解析；
 *   · 结构体只追加字段，旧布局照常可用；
 *   · 事件编号见 MTKPR_EV_*（与 Python 侧 EventKind 一一对应）；
 *   · request_input 的返回码见下方注释——三种失败（超时/取消/无通道）
 *     与「设备拒绝」无关，载荷必须按码分支，不得笼统处理。
 */
#ifndef KERNEL_API_T_H
#define KERNEL_API_T_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* wait_event 的事件编号 */
#define MTKPR_EV_DOWNLOAD_FILE 0
#define MTKPR_EV_FILE_SYS_OP   1
#define MTKPR_EV_UPLOAD_FILE   2
#define MTKPR_EV_START         3
#define MTKPR_EV_END           4
#define MTKPR_EV_OK            5
#define MTKPR_EV_LOG           6
#define MTKPR_EV_TIMEOUT       7
#define MTKPR_EV_DEV_GONE      8

/* request_input 返回码 */
#define MTKPR_REQ_NO_CHANNEL  0   /* 无宿主通道（壳未接 GUI/JSON 宿主） */
#define MTKPR_REQ_TIMEOUT    -1   /* 宿主超时未应答 */
#define MTKPR_REQ_CANCELLED  -2   /* 宿主主动取消：干净中止，不算设备拒绝 */
#define MTKPR_REQ_TOO_SMALL  -3   /* out 缓冲不足 */

typedef struct kernel_api_s {
    void *ctx;
    void (*send)(void *ctx, const uint8_t *data, uint32_t len, uint32_t flag);
    int  (*wait_event)(void *ctx, uint32_t budget_ms,
                       const uint8_t **raw, uint32_t *len);
    void (*reply)(void *ctx, const uint8_t *data, uint32_t len);
    uint32_t (*supply)(void *ctx, const uint8_t *data, uint32_t len,
                       int32_t decl_len);
    uint32_t (*recv_file)(void *ctx, const char *path);
    uint32_t (*require)(void *ctx, const char *kind,
                        uint8_t *out, uint32_t cap);
    void (*enter_loop)(void *ctx);
    void (*sleep_ms)(void *ctx, uint32_t ms);
    void (*log)(void *ctx, const char *level, const char *text);
    int  (*attached)(void *ctx);
    void (*serve_da2)(void *ctx);
    /* 运行途中向宿主索取输入（如签名应答）：阻塞到回应。
     * 返回 >0 = 写入 out 的字节数；<=0 见 MTKPR_REQ_*。
     * info 只带回合类型标签（如 "SEC-POLICY"），不带任何设备原文。 */
    int32_t (*request_input)(void *ctx, const char *kind, const char *info,
                             uint8_t *out, uint32_t cap);
} kernel_api_t;

#ifdef __cplusplus
}
#endif
#endif /* KERNEL_API_T_H */
