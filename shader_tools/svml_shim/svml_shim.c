/* DataRaiders: exposes MSVC SVML vector math and SSE rcp to ctypes. Arrays of n floats, 4-lane blocks, tail zero-padded. */
#include <immintrin.h>
#define EXPORT __declspec(dllexport)
#define UNARY(NAME, EXPR) \
EXPORT void NAME(const float* a, float* out, int n) { \
    int i = 0; \
    for (; i + 4 <= n; i += 4) { __m128 x = _mm_loadu_ps(a + i); _mm_storeu_ps(out + i, EXPR); } \
    if (i < n) { float ta[4] = {0, 0, 0, 0}, to[4]; int j; \
        for (j = 0; i + j < n; ++j) ta[j] = a[i + j]; \
        { __m128 x = _mm_loadu_ps(ta); _mm_storeu_ps(to, EXPR); } \
        for (j = 0; i + j < n; ++j) out[i + j] = to[j]; } }
#define BINARY(NAME, EXPR) \
EXPORT void NAME(const float* a, const float* b, float* out, int n) { \
    int i = 0; \
    for (; i + 4 <= n; i += 4) { __m128 x = _mm_loadu_ps(a + i); __m128 y = _mm_loadu_ps(b + i); _mm_storeu_ps(out + i, EXPR); } \
    if (i < n) { float ta[4] = {0, 0, 0, 0}, tb[4] = {0, 0, 0, 0}, to[4]; int j; \
        for (j = 0; i + j < n; ++j) { ta[j] = a[i + j]; tb[j] = b[i + j]; } \
        { __m128 x = _mm_loadu_ps(ta); __m128 y = _mm_loadu_ps(tb); _mm_storeu_ps(to, EXPR); } \
        for (j = 0; i + j < n; ++j) out[i + j] = to[j]; } }
UNARY(dr_sin, _mm_sin_ps(x))
UNARY(dr_cos, _mm_cos_ps(x))
UNARY(dr_tan, _mm_tan_ps(x))
UNARY(dr_exp, _mm_exp_ps(x))
UNARY(dr_exp2, _mm_exp2_ps(x))
UNARY(dr_log, _mm_log_ps(x))
UNARY(dr_log2, _mm_log2_ps(x))
UNARY(dr_rcp, _mm_rcp_ps(x))
BINARY(dr_atan2, _mm_atan2_ps(x, y))
BINARY(dr_pow, _mm_pow_ps(x, y))
BINARY(dr_fmod, _mm_fmod_ps(x, y))
EXPORT int dr_shim_version(void) { return 1; }
