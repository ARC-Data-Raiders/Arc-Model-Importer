StructuredBuffer<float4> b : register(t0); float4 main(float4 p : SV_Position) : SV_Target0 { return b[(uint)p.x]; }
