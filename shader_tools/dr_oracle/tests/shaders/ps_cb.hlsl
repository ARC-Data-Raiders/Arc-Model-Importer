cbuffer C : register(b0) { float4 c; }; float4 main(float4 p : SV_Position) : SV_Target0 { return c; }
