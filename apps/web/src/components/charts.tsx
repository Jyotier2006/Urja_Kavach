'use client';
import {useEffect,useRef} from 'react';
import * as echarts from 'echarts/core';
import {LineChart,BarChart,HeatmapChart} from 'echarts/charts';
import {GridComponent,TooltipComponent,LegendComponent,MarkLineComponent,MarkAreaComponent,VisualMapComponent,DataZoomComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
import type {EChartsOption} from 'echarts';
echarts.use([LineChart,BarChart,HeatmapChart,GridComponent,TooltipComponent,LegendComponent,MarkLineComponent,MarkAreaComponent,VisualMapComponent,DataZoomComponent,CanvasRenderer]);
export function Chart({option,height=230,label}:{option:EChartsOption;height?:number;label:string}){
 const ref=useRef<HTMLDivElement>(null);const chart=useRef<echarts.ECharts|null>(null);
 useEffect(()=>{if(!ref.current)return;chart.current=echarts.init(ref.current);const observer=new ResizeObserver(()=>chart.current?.resize());observer.observe(ref.current);return()=>{observer.disconnect();chart.current?.dispose();};},[]);
 useEffect(()=>{chart.current?.setOption({animation:!window.matchMedia('(prefers-reduced-motion: reduce)').matches,animationDuration:300,textStyle:{fontFamily:'Inter',fontSize:11,color:'#6f7d74'},grid:{top:32,left:44,right:20,bottom:32},// confine keeps the tooltip inside the chart's own rect. Panels are overflow:hidden, so an
// unconfined tooltip near an edge gets clipped and its numbers become unreadable.
tooltip:{trigger:'axis',confine:true,backgroundColor:'#fff',borderColor:'#dce8da',textStyle:{color:'#203c32'},extraCssText:'max-width:230px;white-space:normal;box-shadow:0 2px 10px rgba(32,60,50,.12)'},...option},true);},[option]);
 return <div ref={ref} style={{height,width:'100%'}} role="img" aria-label={label}/>;
}
export function lineOption(labels:(string|number)[],series:{name:string;data:(number|null)[];color:string;dashed?:boolean;area?:boolean}[],yName='',marker?:number):EChartsOption{return {legend:{top:0,right:12,itemWidth:16,itemHeight:7,textStyle:{color:'#617166',fontSize:10}},xAxis:{type:'category',data:labels,boundaryGap:false,axisLine:{lineStyle:{color:'#e2e7de'}},axisTick:{show:false},axisLabel:{color:'#778579',fontSize:10}},yAxis:{type:'value',name:yName,nameTextStyle:{fontSize:10},axisLabel:{color:'#778579',fontSize:10},splitLine:{lineStyle:{color:'#eef1eb'}}},series:series.map((s,i)=>({type:'line',name:s.name,data:s.data,symbol:'none',smooth:.2,lineStyle:{color:s.color,width:2,type:s.dashed?'dashed':'solid'},itemStyle:{color:s.color},...(s.area?{areaStyle:{color:s.color,opacity:.08}}:{}),...(marker!==undefined&&i===0?{markLine:{silent:true,symbol:'none',data:[{yAxis:marker}],lineStyle:{color:'#CE703B',type:'dashed',width:1},label:{formatter:`Limit ${marker}`,fontSize:10}}}:{})}))};}
