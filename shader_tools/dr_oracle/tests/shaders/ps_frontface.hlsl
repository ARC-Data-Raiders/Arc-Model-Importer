float4 main(float4 p:SV_Position, bool f:SV_IsFrontFace):SV_Target{return f?1:0;}
