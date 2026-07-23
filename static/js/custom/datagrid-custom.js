// All customized client-side jscripts for EasyUI DataGrid.
	var objectToCSVRow = function(dataObject) {
		var dataArray = new Array;
		for (var o in dataObject) {
			var innerValue = dataObject[o]===null?'':dataObject[o].toString();
			var result = innerValue.replace(/"/g, '""');
			result = '"' + result + '"';
			dataArray.push(result);
		}
		return dataArray.join(' ') + '\r\n';
	}

	var exportToCSV = function(arrayOfObjects) {
		if (!arrayOfObjects.length) {
			return;
		}
		var csvContent = "data:text/csv;charset=utf-8,";
		// headers
		csvContent += objectToCSVRow(Object.keys(arrayOfObjects[0]));

		arrayOfObjects.forEach(function(item){
			csvContent += objectToCSVRow(item);
		}); 

		var encodedUri = encodeURI(csvContent);
		var link = document.createElement("a");
		link.setAttribute("href", encodedUri);
		link.setAttribute("download", "customers.csv");
		document.body.appendChild(link); // Required for FF
		link.click();
		document.body.removeChild(link); 
	}

	function jsonconvertstrings(alist){
		jsonlist = JSON.stringify(alist)
		
		// To do reverse-conversion in python, use alist = json.loads(jsonlist) 
		return jsonlist
		
		// convert a list/array of strings such as ["id1","id2"] into a json string 
		var jsonlist = '[';
		for(var i=0;i<alist.length;i++){
			var tem = alist[i];
			jsonlist = jsonlist + '"' + tem + '",';
		}
		if(jsonlist.length > 1){
			// remove last ","
			jsonlist = jsonlist.substring(0,jsonlist.length-1);
		}
		jsonlist = jsonlist + ']';
		//alert(json);
		return jsonlist;
	}

	function downloadTable(dg, downloadallterms, url_download) {
		alert("downloadTable");
		// var dg = $('#dg')
		// var url_download = '/alumni/download/'
		// downloadallterms = 0, or 1
        var newrows = dg.datagrid('getRows');
		// download only those rows shown in the table
		//alert("download some");
		if (newrows.length==0){
			alert('No record in the table is selected for download.');
			return;
		}
		alert(newrows.length);
		var allPosIds = [];
		for(var i=0; i<newrows.length; i++){
			var row = newrows[i];
			//alert(row.GrantComponentPK);
			allPosIds.push(row.id);
		}
		
		var allids = jsonconvertstrings(allPosIds);
		
		//alert(newrows.length);
		$.get(url_download,
			{
				allids: allids,
				downloadallterms: downloadallterms
			},
			function(data){
				var obj = jQuery.parseJSON(data);
				var msg = obj.message;
				var link = obj.link;
				var status = obj.status;
				if(!status){
					var msg2 = "Error in downloading table: <br/><br/>";
					msg2 += msg;
					$.messager.alert('Download table',msg2,'info');
					return;
				}
				else{
					window.location.href = link; 
					return;
				}
				//window.location.href = '/alumni/alumnidb/'; 
			}
		);
    }
		
	function downloadTablePresent(dg, url_download) {
		downloadTable(dg, 0, url_download);
		return;
		
		var msg = 'Do you want to retrieve all records?\n Click "No" for records on the current page';
		$.messager.defaults.ok = 'Yes';
		$.messager.defaults.cancel = 'No';
		$.messager.confirm('Download table', msg, function(r){
			downloadTable(dg, 0, url_download);
		});
	}
	
	function downloadTableAll(dg, url_download) {
		downloadTable(dg, 1, url_download);
		return;
		
		var msg = 'Do you want to retrieve all records?\n Click "No" for records on the current page';
		$.messager.defaults.ok = 'Yes';
		$.messager.defaults.cancel = 'No';
		$.messager.confirm('Download table', msg, function(r){
			downloadTable(dg, 1, url_download);
		});
	}
	
	function uploadDialogue(dlg) {
		// var dlg = $('#dlg_alumni');
		dlg.dialog('open');
		return;
    }
		
	function cancelDialogue(dlg){
		// var dlg = $('#dlg_alumni');
		dlg.dialog('close');
		return;
	}
	
	function upload(dlg, url_upload){
		//var url_upload = '/alumni/alumniupload/'
		
		var win = $.messager.progress({
			title:'Please waiting',
			msg:'Uploading file ...'
		});
        setTimeout(function(){
            $.messager.progress('close');
        },50000)
		var excelfile = document.getElementById('id_excelname').files[0];
		if (!excelfile) {
			//$.messager.progress('close');
			alert("Error: No valid excel file is selected.");
			return;
		}
		//else{
		//	alert("Name: " + excelfile.name + "\n" + "Last Modified Date :" + excelfile.lastModifiedDate);
		//}
		
		var csrf_token = $('input[name="csrfmiddlewaretoken"]').val();
		//var formdata = new FormData();
		var formdata = new FormData($('form').get(0));
		formdata.append('excelfile_upload', excelfile);
		formdata.append('filename', excelfile.name);
		formdata.append('csrfmiddlewaretoken', '{{ csrf_token }}');
		$.ajax(url_upload,
			{
				//csrfmiddlewaretoken: csrf_token,
				type: 'POST',
				enctype: "multipart/form-data",
				data: formdata,
				processData: false,
				contentType: false,
				//contentType: 'multipart/form-data',
				success: console.log('success!')
			}
				//);
		)
		.done(function( data ) {
			var obj = jQuery.parseJSON(data);
			var msg = obj.msg;
			var status = obj.status;
			var link = obj.link;
			//alert(status);
			$.messager.progress('close');
			if(status){
				alert(msg);
				cancelDialogue(dlg);
				//window.location.href = '/alumni/alumnidb/';
				window.location.href = link;
			}
			else{
				alert(msg);
				window.location.href = link;
			}
			return;
		});
		return;
	}
	
        var editIndex = undefined;
        function endEditing(dg){
            if (editIndex == undefined){return true}
            if (dg.datagrid('validateRow', editIndex)){
                dg.datagrid('endEdit', editIndex);
                editIndex = undefined;
                return true;
            } else {
                return false;
            }
        }
        function onClickCell(index, field){
            if (editIndex != index){
				var dg = $(this)
                if (endEditing(dg)){
                    dg.datagrid('selectRow', index)
                            .datagrid('beginEdit', index);
                    var ed = dg.datagrid('getEditor', {index:index,field:field});
                    if (ed){
                        ($(ed.target).data('textbox') ? $(ed.target).textbox('textbox') : $(ed.target)).focus();
                    }
                    editIndex = index;
                } else {
                    setTimeout(function(){
                        $(this).datagrid('selectRow', editIndex);
                    },0);
                }
            }
        }
        function onEndEdit(index, row){
            var ed = $(this).datagrid('getEditor', {
                index: index,
                field: 'id'
            });
			
            //row.last_name_ki = $(ed.target).combobox('getText');
        }
		
        function append(dg){
			//alert("Add a record");
			// Add a new row in a datagrid
			// var dg = $('#dg')
            if (endEditing(dg)){
				// this adds the new row at the bottom of the table
                //dg.datagrid('appendRow',{status:'P'});
                dg.datagrid('appendRow',{});
				// this adds the new row at the top of the table
				//dg.datagrid('insertRow',{index:0});
                editIndex = dg.datagrid('getRows').length-1;
                dg.datagrid('selectRow', editIndex)
                        .datagrid('beginEdit', editIndex);
            }
        }
        function removeit(dg, url_delete){
			//alert(url_delete);
			var status = deleteSelectedFromDB(dg, url_delete);
			//alert(status);
			return;
			
            if (editIndex == undefined){return}
			
			dg.datagrid('cancelEdit', editIndex)
                .datagrid('deleteRow', editIndex);
			editIndex = undefined;
			
        }
        function accept(dg, url_save){
            if (endEditing(dg)){
				getChanges(dg);
				var status = saveSelectedIntoDB(dg, url_save);
				if(status){
					dg.datagrid('acceptChanges');
				}
            }
        }
        function reject(dg){
            dg.datagrid('rejectChanges');
            editIndex = undefined;
        }
        function getChanges(dg){
            var rows = dg.datagrid('getChanges');
            //alert(rows.length+' rows are changed!');
        }
		
		function saveSelectedIntoDB(dg, url_save) {
			// save the row in editing into DB
			//var ss = [];
			//var ids = [];
			var records = [];
			var rows = dg.datagrid('getChanges');
			if (rows.length==0){
				alert("You have not entered any new person!");
				return false;
			}
			else{
				//alert(rows.length+' persons are entered!');
			}
			for(var i=0; i<rows.length; i++){
				var row = rows[i];
				//alert(row.cancer_related);
				//alert(row.id);
				var record = {}
				for(var key in row) {
					var value = row[key];
					///alert(value);
					record[key] = value;
				}
				//alert(row.id);
				//ids.push(row.id);
				records.push(record);
			}
			var msg = 'You have entered '
				+ rows.length
				+ ' records for saving. Do you really want to save them '
				//+ ids.join()
				+ '?';
			$.messager.confirm('Saving record', msg, function(r){
				if (r){
					//alert('Stop: A revision is in progress!');
					//return
				}
				else{
					return false;
				}
							
				var win = $.messager.progress({
					title:'Please waiting',
					msg:'Saving record ...'
				});
				
				//setTimeout(function(){
				//    $.messager.progress('close');
				//},5000)
				
				//alert('confirmed: '+r);
				//alert(statusType);
				//var IDs = jsonconvertstrings(ids);
				$.get(url_save,
					{
						//ids: IDs,
						records: JSON.stringify(records)
					},
					function(data){
						$.messager.progress('close');
						var obj = jQuery.parseJSON(data);
						var msg = obj.msg;
						//alert(msg);
						var link = obj.link;
						//alert(link);
										
						var status = obj.status;
						if(!status){
							//alert(msg);
							var msg2 = "Error in saving record. <br/><br/>";
							msg2 += msg;
							//msg2 += "<br/><br/>Refer to the csv file returned for more information.";
							//$.messager.alert('Saving record',msg2,'info');
							alert(msg);
							window.location.href = link;
							return false;
						} 
						else{
							$.messager.alert('Saving record',msg,'info');
						}
						//var msg = 'The following records have been saved into DB: ' + ids.join() + '!';
						//$.messager.alert('Deleting person',msg,'info');
						window.location.href = link;
						//window.location.href = "/alumni/alumnidbs/";
						// instead of refreshing the table, just delete the row from table
						
						//dg.datagrid('cancelEdit', editIndex)
						//	.datagrid('deleteRow', editIndex);
						//editIndex = undefined;
						
						return true;
					}
				);
				return true;
			});
			return true;
		}
		
		
		function deleteSelectedFromDB(dg, url_delete) {
			// var dg = $('#dg')
			// var url_delete = '/alumni/delete/'
			// Delete one row that is in cell editing
			//var ss = [];
			//var ids = [];
			var records = [];
			var ids = [];
			var rows = dg.datagrid('getSelections');
			for(var i=0; i<rows.length; i++){
				var row = rows[i];
				if (row.id !== undefined && row.id !== null) {
					ids.push(row.id);
				}
				
				var record = {};
				for(var key in row) {
					var value = row[key];
					//alert(value);
					record[key] = value;
				}
				records.push(record);
			}
		
			if (rows.length==0){
				alert("You have not selected any row for deletion!");
				return false;
			}
		
			var msg = 'You have selected '
				+ rows.length
				+ ' records for deletion. Do you really want to delete them ? ';
			$.messager.confirm('Deleting record', msg, function(r){
				if (r){
					//alert('Stop: A revision is in progress!');
					//return
				}
				else{
					return false;
				}
							
				var win = $.messager.progress({
					title:'Please waiting',
					msg:'Deleting records ...'
				});
				
				//setTimeout(function(){
				//    $.messager.progress('close');
				//},5000)
				
				//alert('confirmed: '+r);
				//alert(statusType);
				//var IDs = jsonconvertstrings(ids);
				$.get(url_delete,
					{
						ids: JSON.stringify(ids),
						records: JSON.stringify(records)
					},
					function(data){
						$.messager.progress('close');
						var obj = jQuery.parseJSON(data);
						var msg = obj.msg;
						var link = obj.link;
						//alert(link);
										
						var status = obj.status;
						if(!status){
							//alert(msg);
							var msg2 = "Error in deleting record. <br/><br/>";
							msg2 += msg;
							//msg2 += "<br/><br/>Refer to the csv file returned for more information.";
							$.messager.alert('Deleting record',msg2,'info');
							//window.location.href = link;
							//window.location.href = "/grant/funding/";
							return false;
						} 
				
						//var msg = 'The following records have been deleted from DB: ' + ids.join() + '!';
						//$.messager.alert('Deleting person',msg,'info');
						//window.location.href = link;
						//window.location.href = "/alumni/alumnidbs/";
						// instead of refreshing the table, just delete the row from table
						
						window.location.href = link;
						//dg.datagrid('cancelEdit', editIndex)
						//	.datagrid('deleteRow', editIndex);
						//editIndex = undefined;
						
						return true;
					}
				);
				return true;
			});
			return true;
		}


// === NExtSEEK sample-search UI rework helpers =========================
// Shared by the Simple (#simple_dgtable) and Advanced (#advanced_dgtable)
// search grids. Added 2026-07: discoverable column filters, ellipsis+tooltip
// cells, and a reset control. Frontend-only; no backend contract change.

function nsEscapeHtml(value) {
	return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
		return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
	});
}

// EasyUI column formatter: render a single-line, ellipsis-truncated cell whose
// full (escaped) value is exposed via the native title tooltip on hover.
function nsEllipsisFormatter(value, row, index) {
	var s = nsEscapeHtml(value);
	return '<span class="ns-ellip" title="' + s + '">' + s + '</span>';
}

// Enable an always-visible filter row: a 'contains' text filter on each field
// in textFields, and a no-input 'label' filter on each field in labelFields
// (needed to suppress the default text filter EasyUI would otherwise add to
// structural columns like the checkbox and id).
function nsEnableColumnFilters(dg, textFields, labelFields) {
	var specs = [];
	(labelFields || []).forEach(function (f) { specs.push({ field: f, type: 'label' }); });
	(textFields || []).forEach(function (f) { specs.push({ field: f, type: 'text' }); });
	dg.datagrid('enableFilter', specs);
}

// Reset a search tab: clear each input (handles easyui combobox, easyui
// textbox, and plain inputs) and remove any active column filters from dg.
function nsResetSearch(dg, inputSelectors) {
	(inputSelectors || []).forEach(function (sel) {
		var $el = $(sel);
		if (!$el.length) { return; }
		if ($el.data('combobox')) { try { $el.combobox('clear'); return; } catch (e) {} }
		if ($el.data('textbox'))  { try { $el.textbox('clear');  return; } catch (e) {} }
		$el.val('');
	});
	if (dg) { try { dg.datagrid('clearFilter'); } catch (e) {} }
}
