#!/usr/bin/env python
import logging
logger = logging.getLogger(__name__)

from .models import Sample_attributes
from .dbtable_attributetype import DBtable_attributetype
from dmac.dbtable import DBtable
from dmac.conversion import toDateClass, is_numeric, toFloat, toBinaryTinyInt, toString, getDefaultDate

SAMPLEATTRIBUTE_FILTER_MAPPING = {
}

SAMPLEATTRIBUTE_DEFAULT = {
}

# The operator set offered for each attribute-type id. Every set lists its
# default operator first, which is why getOperators marks options[0] selected.
#
# LATENT_BUGS #24 lives here. The if/elif ladder this replaced tested
# `attributeType_id==3 or attributeType_id==3` -- a tautology where a second id
# was probably meant. {3} reproduces the behaviour exactly; the intended second
# id is unknown, so do not guess it.
OPERATOR_SETS = (
    ({3}, ['No Filter', 'Equal', 'Not Equal', 'Less', 'Greater', 'Between'],
     'numeric value', 'numeric value', 'numeric'),
    ({5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 19, 20, 21},
     ['Contain', 'Not Contain', 'No Filter'],
     'string value', 'not in use', 'string'),
    ({1, 2}, ['No Filter', 'Equal', 'Not Equal', 'Before', 'After', 'Between'],
     'mm/dd/year', 'mm/dd/year', 'date'),
    ({15}, ['No Filter', 'True', 'False'], 'not in use', 'not in use', 'bool'),
)

# Attribute type 18 had a branch of its own whose options, placeholders and
# filter_type were identical to the fallback, so it now falls through to it.
OPERATORS_DEFAULT = (['No Filter', 'Contain', 'Not Contain'],
                     'string value', 'not in use', 'string')

# Each filter rule as a predicate over the converted value, the converted
# filter_valueFrom and the converted filter_valueTo. A rule that is not listed
# -- including 'No Filter' -- passes every value, as the ladders' else branches
# did.
STRING_RULES = {
    'Contain':     lambda vi, frm, to: frm in vi,
    'Not Contain': lambda vi, frm, to: frm not in vi,
}

BOOL_RULES = {
    'True':  lambda vi, frm, to: vi == 1,
    'False': lambda vi, frm, to: vi != 1,
}

NUMERIC_RULES = {
    'Equal':     lambda vi, frm, to: vi == frm,
    'Not Equal': lambda vi, frm, to: vi != frm,
    # 'Less' and 'Greater' are inclusive. That is what the ladder did.
    'Less':      lambda vi, frm, to: vi <= frm,
    'Greater':   lambda vi, frm, to: vi >= frm,
    'Between':   lambda vi, frm, to: vi >= frm and vi <= to,
}

# Only 'Not Equal' passes a value that will not parse as a date; every other
# rule rejects it. That asymmetry is deliberate here because it was there before.
DATE_RULES = {
    'Equal':     lambda vi, frm, to: vi is not None and vi == frm,
    'Not Equal': lambda vi, frm, to: vi is None or vi != frm,
    'Before':    lambda vi, frm, to: vi is not None and vi <= frm,
    'After':     lambda vi, frm, to: vi is not None and vi >= frm,
    'Between':   lambda vi, frm, to: vi is not None and vi >= frm and vi <= to,
}


def operatorData(msg, status, filter_rule, placeholder_start='',
                 placeholder_end='', filter_type=''):
    """The dict getOperators returns, on both its error and its success paths."""
    return {
        'msg': msg,
        'status': status,
        'filter_rule': filter_rule,
        'placeholder_start': placeholder_start,
        'placeholder_end': placeholder_end,
        'filter_type': filter_type,
    }


class DBtable_sampleattribute(DBtable):
    def __init__(self, whichServer='default'):
        DBtable.__init__(self, 'SEEK', 'seek_development')
        
        self.tablename = 'sample_attributes'
        self.tablemodel = Sample_attributes
        self.fulltablename = self.tablemodel
        self.viewtablename = self.dbname + '.' + self.tablename
        self.fields = [
            'id',
            'title',
            'sample_attribute_type_id',
            'required',
            'created_at',
            'updated_at',
            'pos',
            'sample_type_id',
            'unit_id',
            'is_title',
            'template_column_index',
            'original_accessor_name',
            'sample_controlled_vocab_id',
            'linked_sample_type_id',
            'pid',
            'description',
            'isa_tag_id',
            'allow_cv_free_text',
            'template_attribute_id',
        ]
        
        self.uniqueFields = ['title', 'sample_type_id']
        self.primaryField = "id"
        self.fieldMapping = SAMPLEATTRIBUTE_FILTER_MAPPING
        
    def _normalizeSampleTypeIds(self, sample_type_ids):
        """Sorted, deduped, positive ints. Shared by both bulk lookups so the
        id-coercion rule lives in exactly one place."""
        ids = []
        for sid in sample_type_ids or []:
            try:
                ids.append(int(sid))
            except Exception:
                continue
        return sorted({i for i in ids if i > 0})

    def _bulkAttributeRows(self, ids, columns):
        """(sample_type_id, *columns) ordered by (sample_type_id, pos, id)."""
        return (
            self.tablemodel.objects.filter(sample_type_id__in=ids)
            .order_by("sample_type_id", "pos", "id")
            .values_list("sample_type_id", *columns)
        )

    def _validBulkAttributeRows(self, ids, columns):
        """Rows from _bulkAttributeRows(), with the skip/clean rules shared by
        both bulk lookups applied once: drop rows whose sample_type_id doesn't
        coerce to a positive-ish int, drop None titles, strip and drop blank
        titles. Yields (sample_type_id, cleaned_title, *rest) -- `rest` holds
        whatever extra columns were requested beyond "title", in order -- so
        each public method can be a thin accumulation over this.
        """
        for row in self._bulkAttributeRows(ids, columns):
            sid, title = row[0], row[1]
            rest = row[2:]
            try:
                sid_int = int(sid)
            except Exception:
                continue
            if title is None:
                continue
            t = str(title).strip()
            if not t:
                continue
            yield (sid_int, t) + rest

    def getAttributeTitlesBySampleTypeIds(self, sample_type_ids):
        """
        Bulk fetch attribute titles for many sample types.

        Returns:
            dict[int, list[str]] mapping sample_type_id -> ordered attribute titles (by pos).

        Notes:
            - Uses Django ORM (no raw SQL).
            - Ordering is stable: (sample_type_id, pos, id).
        """
        ids = self._normalizeSampleTypeIds(sample_type_ids)
        if not ids:
            return {}

        out = {sid: [] for sid in ids}
        for sid_int, title in self._validBulkAttributeRows(ids, ["title"]):
            if sid_int not in out:
                out[sid_int] = []
            out[sid_int].append(title)
        return out

    def getAttributeSpecsBySampleTypeIds(self, sample_type_ids):
        """Same query and ordering as getAttributeTitlesBySampleTypeIds, plus the
        `required` flag and `pos`.

        A blank-template sheet needs to mark required columns, which the titles
        method deliberately does not carry. Kept as a sibling rather than a wider
        return type because that method has a live caller
        (nextseek_api/services/entity_tree.py) and tests that assert its shape.

        Returns:
            dict[int, list[dict]] -- {"title": str, "required": bool, "pos": int}
        """
        ids = self._normalizeSampleTypeIds(sample_type_ids)
        if not ids:
            return {}

        out = {sid: [] for sid in ids}
        for sid_int, title, required, pos in self._validBulkAttributeRows(
            ids, ["title", "required", "pos"]
        ):
            if sid_int not in out:
                out[sid_int] = []
            out[sid_int].append({
                "title": title,
                "required": bool(required),
                "pos": int(pos) if pos is not None else 0,
            })
        return out

    
    def getAttributeInfo(self, sampleType_id):
        attritype = DBtable_attributetype()
        attributeTypeNames = attritype.getAttributeTypes()
        diclist = self.db.retrieveRecords(self.tablemodel, 'sample_type_id', sampleType_id)
        from operator import itemgetter
        newlist = sorted(diclist, key=itemgetter('pos'))
        attributeInfo = {}
        headers = []    
        headers_required = []   
        attributeTypes = {}     
        diclist_new = []
        for dici in newlist:
            field = dici['title']
            headers.append(field)
            
            isrequired = dici['required']
            if isrequired:
                headers_required.append(field)
                
            attributeTypes[field] = dici['sample_attribute_type_id']
            
            dici_new = {}
            dici_new['title'] = dici['title']
            dici_new['id'] = dici['id']
            dici_new['pos'] = dici['pos']
            atype_id = dici['sample_attribute_type_id']
            dici_new['sample_attribute_type_id'] = atype_id
            if atype_id in attributeTypeNames:
                dici_new['sample_attribute_type_title'] = attributeTypeNames[atype_id]
            elif int(atype_id) in attributeTypeNames:
                dici_new['sample_attribute_type_title'] = attributeTypeNames[int(atype_id)]
            else:
                dici_new['sample_attribute_type_title'] = ''
            dici_new['required'] = toBinaryTinyInt(dici['required'])
            dici_new['is_title'] = toBinaryTinyInt(dici['is_title'])
            dici_new['sample_controlled_vocab_id'] = dici['sample_controlled_vocab_id']
            dici_new['description'] = dici.get('description') or ''
            diclist_new.append(dici_new)
            
        attributeInfo['diclist'] = diclist_new
        attributeInfo['headers'] = headers
        attributeInfo['headers_required'] = headers_required
        attributeInfo['attributeTypes'] = attributeTypes
        
        from .dbtable_sampletype import DBtable_sampletype  # deferred: circular import, see dbtable_sampletype.py:12
        sampletype = DBtable_sampletype("DEFAULT")
        attributeInfo['sampletype'] = sampletype.getOneRecord(sampleType_id)
        attributeInfo['sampleType_id'] = sampleType_id
        return attributeInfo
        
    def getAttributes(self, sampletype_id, valueSelected=''):
        attributeInfo = self.getAttributeInfo(sampletype_id)
        headers = attributeInfo['headers']
        if len(headers)==0:
            msg = "Error: the sample type has no attribute defined. "
            status = 0
            options = []
        else:
            msg = "Attributes retrieved "
            status = 1
            attributeTypes = attributeInfo['attributeTypes']
            
            options = []
            selected = False
            id = 0
            for header in headers:
                attributeType_id = attributeTypes[header]
                option = {}
                id += 1
                option['name'] = header
                option['attribute'] = header
                if str(attributeType_id)==str(valueSelected):
                    option['selected'] = True
                    selected = True
                
                options.append(option)
        
            if not selected:
                option = {}
                option['name'] = valueSelected
                option['attribute'] = 'none'
                option['selected'] = True
            
                options = [option] + options
            
        attrOptions = options
        data = {'msg':msg, 'status': status, 'attrOptions':attrOptions}
        data['valueSelected'] = valueSelected
        if valueSelected=='yes':
            data['rows'] = attributeInfo['diclist']
        return data
        
    def getOperators(self, sampletype_id, attribute):
        attributeInfo = self.getAttributeInfo(sampletype_id)
        headers = attributeInfo['headers']
        if len(headers)==0:
            return operatorData("Error: the sample type has no attribute defined. ", 0, [])

        attributeTypes = attributeInfo['attributeTypes']
        if attribute not in attributeTypes:
            return operatorData("Error: the sample attribute not available. ", 0, [])

        attributeType_id = int(attributeTypes[attribute])
        names, placeholder_start, placeholder_end, filter_type = OPERATORS_DEFAULT
        for ids, operators, start, end, ftype in OPERATOR_SETS:
            if attributeType_id in ids:
                names = operators
                placeholder_start, placeholder_end, filter_type = start, end, ftype
                break

        options = [{'name': name, 'operator': name} for name in names]
        options[0]['selected'] = True
        return operatorData("Attributes retrieved ", 1, options,
                            placeholder_start, placeholder_end, filter_type)
    
    def filterString(self, values, filter_rule, filter_valueFrom, filter_valueTo):
        pred = STRING_RULES.get(filter_rule)
        if pred is None:
            return [True for _ in values]
        return [pred(toString(value), filter_valueFrom, None) for value in values]

    def filterBool(self, values, filter_rule, filter_valueFrom, filter_valueTo):
        pred = BOOL_RULES.get(filter_rule)
        if pred is None:
            return [True for _ in values]
        return [pred(toBinaryTinyInt(value), filter_valueFrom, None) for value in values]

    def filterNumeric(self, values, filter_rule, filter_valueFrom, filter_valueTo):
        valueFrom = toFloat(filter_valueFrom)
        pred = NUMERIC_RULES.get(filter_rule)
        if pred is None:
            return [True for _ in values]
        valueTo = toFloat(filter_valueTo) if filter_rule=='Between' else None
        return [pred(toFloat(value), valueFrom, valueTo) for value in values]

    def filterDate(self, values, filter_rule, filter_valueFrom, filter_valueTo):
        valueFrom = toDateClass(filter_valueFrom)
        pred = DATE_RULES.get(filter_rule)
        if pred is None:
            return [True for _ in values]
        valueTo = toDateClass(filter_valueTo) if filter_rule=='Between' else None
        return [pred(toDateClass(value), valueFrom, valueTo) for value in values]

    def filterValues(self, values, sampletype_id, attribute, filter_rule, filter_valueFrom, filter_valueTo):
        attrdata = self.getOperators(sampletype_id, attribute)
        filter_type = attrdata['filter_type']
          
        if filter_type=='string':
            passvalues = self.filterString(values, filter_rule, filter_valueFrom, filter_valueTo)
        elif filter_type=='numeric':
            passvalues = self.filterNumeric(values, filter_rule, filter_valueFrom, filter_valueTo)
        elif filter_type=='date':
            passvalues = self.filterDate(values, filter_rule, filter_valueFrom, filter_valueTo)
        elif filter_type=='bool':
            passvalues = self.filterBool(values, filter_rule, filter_valueFrom, filter_valueTo)
        else:
            passvalues = self.filterString(values, filter_rule, filter_valueFrom, filter_valueTo)
            
        return passvalues
        
    def validateFilters(self, sampletype_id, attribute, filter_rule, filter_valueFrom, filter_valueTo):
        if filter_rule=='No Filter':
            msg = 'okay'
            status = 1
            return msg, status
        
        attrdata = self.getOperators(sampletype_id, attribute)
        msg = attrdata['msg']
        status = attrdata['status']
        if status==0:
            return msg, status
        
        filter_type = attrdata['filter_type']
        if filter_type=='numeric':
            if not is_numeric(filter_valueFrom):
                msg = 'Warning: Not a valid numeric value: ' + str(filter_valueFrom)
                status = 0
                return msg, status
            
            if filter_rule=='Between':
                if not is_numeric(filter_valueTo):
                    msg = 'Warning: Not a valid numeric value: ' + str(filter_valueTo)
                    status = 0
                    return msg, status
            
        elif filter_type=='date':
            if toDateClass(filter_valueFrom) is None:
                msg = 'Warning: Start date not valid: ' + str(filter_valueFrom)
                status = 0
                return msg, status
            
            if filter_rule=='Between':
                if toDateClass(filter_valueTo) is None:
                    msg = 'Warning: End date not valid: ' + str(filter_valueTo)
                    status = 0
                    return msg, status
        
        msg = 'validateFilters: okay'
        status = 1
        return msg, status
        
    def reformatRecordForDB(self, login_user, record):
        record_new = {}
        for key in self.fields:
            key_new = str(key)
            if key in record:
                value = record[key]
            elif key=="sample_attribute_type_id":
                value = record['sample_attribute_type_title']
            elif key=="created_at":
                value = getDefaultDate()
            elif key=="updated_at":
                value = getDefaultDate()
            elif key=="template_column_index":
                value = record['pos']
            elif key=="original_accessor_name":
                title = record['title']
                value = title.lower()
            else:
                continue
            
            if key_new=='id':
                pid = int(value)
                if pid>0:
                    record_new[key_new] = value
            elif key_new=="sample_controlled_vocab_id":
                continue
            else:
                record_new[key_new] = value
        return record_new
        
    def getAttributesRenamed(self, sampleType_id, records_revised):
        attributeInfo = self.getAttributeInfo(sampleType_id)
        attributes_db = attributeInfo['diclist']
        attributes_old = {}
        for attribute in attributes_db:
            id = attribute['id']
            attributes_old[id] = attribute
        
        attri_renamed= {}
        for record in records_revised:
            title_new = record['title']
            if 'id' not in record:
                continue
                
            id = int(record['id'])
            if id in attributes_old:
                attribute = attributes_old[id]
                title_old = attribute['title']
                if title_new!=title_old:
                    attri_renamed[title_new] = title_old
        
        return attri_renamed
        
