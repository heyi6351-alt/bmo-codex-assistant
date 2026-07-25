/**
 * @file app_guardian.c
 * @brief Distress detection + phone alert. See app_guardian.h.
 *
 * >>> EDIT THIS ONE LINE <<< : set your ntfy topic, then install the free
 * "ntfy" app on a phone and subscribe to the SAME topic. Any distress phrase
 * heard by the device pushes an urgent notification to that phone.
 */
#include "app_guardian.h"
#include "kaleido.h"

#include "tal_api.h"
#include "http_client_interface.h"
#include "ai_audio_player.h"

/***********************************************************
********************* user configuration *******************
***********************************************************/
#define GUARDIAN_NTFY_TOPIC   "Lynx-Companion-Rayane"   // <-- change + subscribe on your phone
#define GUARDIAN_NTFY_HOST     "ntfy.sh"
#define GUARDIAN_NTFY_PORT     443
#define GUARDIAN_DEBOUNCE_MS   30000                        // don't re-alert within 30s
#define GUARDIAN_HTTP_BUF      1536

// Distress phrases (lower-case). Add your own / your language here.
static const char *k_distress[] = {
    "help me", "help", "i fell", "i've fallen", "ive fallen", "fallen down",
    "emergency", "ambulance", "call my", "call someone", "can't breathe",
    "cant breathe", "chest pain", "i'm hurt", "im hurt", "sos", "i need help",
};

/***********************************************************
*********************** worker plumbing ********************
***********************************************************/
typedef struct {
    char text[128];
} GUARDIAN_MSG_T;

static QUEUE_HANDLE  s_queue = NULL;
static THREAD_HANDLE s_worker = NULL;
static uint32_t      s_last_alert_ms = 0;

static void __to_lower(const char *in, char *out, size_t out_sz)
{
    size_t i = 0;
    for (; in[i] && i + 1 < out_sz; i++) {
        char c = in[i];
        out[i] = (c >= 'A' && c <= 'Z') ? (char)(c - 'A' + 'a') : c;
    }
    out[i] = 0;
}

static bool __is_distress(const char *lower)
{
    for (size_t i = 0; i < sizeof(k_distress) / sizeof(k_distress[0]); i++) {
        if (strstr(lower, k_distress[i])) {
            return true;
        }
    }
    return false;
}

// Push an urgent notification to ntfy.sh/<topic>. Blocking — runs in the worker.
static void __send_push(const char *spoken)
{
    char body[192];
    snprintf(body, sizeof(body),
             "Companion heard a call for help at home.\nHeard: \"%s\"", spoken);

    http_client_header_t headers[] = {
        {.key = "Title",    .value = "\xF0\x9F\x86\x98 SOS from Companion"},
        {.key = "Priority", .value = "urgent"},
        {.key = "Tags",     .value = "rotating_light,house"},
    };

    http_client_request_t req = {
        .host          = GUARDIAN_NTFY_HOST,
        .port          = GUARDIAN_NTFY_PORT,
        .path          = "/" GUARDIAN_NTFY_TOPIC,
        .method        = "POST",
        .headers       = headers,
        .headers_count = sizeof(headers) / sizeof(headers[0]),
        .body          = (const uint8_t *)body,
        .body_length   = strlen(body),
        .tls_no_verify = true,           // demo: skip cert pinning
        .timeout_ms    = 8000,
    };

    /* Do NOT pre-allocate resp.buffer: http_client_request() overwrites
     * response->buffer with its own internally-malloc'd block, and
     * http_client_free() frees it. Pre-allocating leaks our buffer and then
     * double-frees. Pass a zeroed struct and free exactly once. */
    http_client_response_t resp = {0};
    http_client_status_t st = http_client_request(&req, &resp);
    PR_NOTICE("guardian push: status=%d http=%u", st, resp.status_code);
    http_client_free(&resp);
}

static void __guardian_worker(void *arg)
{
    (void)arg;
    GUARDIAN_MSG_T msg;
    for (;;) {
        memset(&msg, 0, sizeof(msg));
        if (OPRT_OK == tal_queue_fetch(s_queue, &msg, QUEUE_WAIT_FOREVER)) {
            __send_push(msg.text);
        }
    }
}

/***********************************************************
************************* public API ***********************
***********************************************************/
OPERATE_RET app_guardian_init(void)
{
    OPERATE_RET rt = OPRT_OK;
    TUYA_CALL_ERR_RETURN(tal_queue_create_init(&s_queue, sizeof(GUARDIAN_MSG_T), 4));

    THREAD_CFG_T cfg = {
        .thrdname   = "guardian",
        .priority   = THREAD_PRIO_2,
        .stackDepth = 1024 * 6,   // TLS + HTTP needs a healthy stack
    };
    TUYA_CALL_ERR_RETURN(tal_thread_create_and_start(&s_worker, NULL, NULL, __guardian_worker, NULL, &cfg));
    PR_NOTICE("guardian ready (topic: %s)", GUARDIAN_NTFY_TOPIC);
    return rt;
}

void app_guardian_on_text(const char *text)
{
    if (text == NULL || text[0] == 0 || s_queue == NULL) {
        return;
    }

    char lower[128];
    __to_lower(text, lower, sizeof(lower));
    if (!__is_distress(lower)) {
        return;
    }

    // Debounce so one incident doesn't fire a burst of pushes.
    uint32_t now = tal_system_get_millisecond();
    if (s_last_alert_ms != 0 && (now - s_last_alert_ms) < GUARDIAN_DEBOUNCE_MS) {
        return;
    }
    s_last_alert_ms = now;

    PR_WARN("guardian: DISTRESS heard -> \"%s\"", text);

    // 1) Immediate local response: red alarm face + jump to home, plus a tone.
    kaleido_face_alert();                          // lock-free: requests the alarm face
    ai_audio_player_alert(AI_AUDIO_ALERT_WAKEUP);  // audible "I'm here" alarm tone

    // 2) Hand the push to the worker so we never block the UI on the network.
    GUARDIAN_MSG_T msg = {0};
    strncpy(msg.text, text, sizeof(msg.text) - 1);
    tal_queue_post(s_queue, &msg, 0);
}
